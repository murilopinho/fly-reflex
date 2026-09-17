"""One command to run the whole thing.

    python3 run.py --demo     # no game needed: synthetic notes, just watch the brain
    python3 run.py --dry-run  # reads the real screen, prints presses, injects nothing
    python3 run.py            # live: reads the screen and presses real keys

Three threads: screen capture (~8 Hz, the rate Spectacle allows), the
simulation (1 kHz real-time, 4 independent copies of the real circuit — one
per lane), and Qt drawing the overlay. The sim is the only thing that decides
a keypress; capture just keeps the looming drive fresh.
"""
import os
import signal
import subprocess
import sys
import time

import numpy as np
from PIL import Image
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from fly_brain import FlyBrain, CALIBRATED_INTENSITY
from hotkey import KeyboardWatch, TOGGLE_KEY
from judge import Judge
from lanes import LANES, KEYCODES, autocalibrate
from overlay import Overlay, LANE_NAMES

SIM_HZ = 1000
EMIT_HZ = 60
ARM_HZ = 5.0                # below this a lane is just resting chatter, not a note
FALL_STEPS = 15             # ms of falling rate that count as "the burst crested".
                            # NOTE: strictly falling, so a HOLD note only fires when
                            # the bar ends. Fixing that needs the plateau case — see
                            # NEXT_SESSION.md before building sustain support.
REARM_RISE = 1.30           # climb back this far above the trough = a new note
PRESS_COOLDOWN_S = 0.18
IDLE_GRACE_S = 2.0          # a couple seconds of silence between notes is normal, free
IDLE_PAIN_TAU = 3.0         # seconds — how fast standing still starts to actually hurt
IDLE_PAIN_PEAK = 0.6        # mood drained per SECOND once idle time is really piling up,
                            # same scale as MAX_DOPAMINE — applied per sim step as /SIM_HZ
RATE_TAU = 0.20             # seconds, EMA window. Heavier smoothing than it looks:
                            # the crest rule reads the SHAPE of this curve, so a
                            # smooth curve crests cleanly. At 0.08 the jitter tripped
                            # the falling-counter early and leaked 7 ghost taps.
DRIVE_SCALE = CALIBRATED_INTENSITY * 1.6   # what a note at full strength reads;
                                           # capture and the demo chart both cap here
YDOTOOL_ENV = {**os.environ, "YDOTOOL_SOCKET": os.environ.get("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")}
ZERO_LANES = {n: 0.0 for n in LANE_NAMES}


# --------------------------------------------------------------------------
# environment checks
# --------------------------------------------------------------------------

def dbus_has(name):
    out = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
         "--object-path", "/org/freedesktop/DBus",
         "--method", "org.freedesktop.DBus.ListNames"],
        capture_output=True, text=True,
    ).stdout
    return name in out


def ensure_spectacle():
    if dbus_has("org.kde.Spectacle"):
        return True
    print("starting spectacle capture daemon...")
    subprocess.Popen(["spectacle", "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.25)
        if dbus_has("org.kde.Spectacle"):
            return True
    return False


def check_ydotool():
    sock = YDOTOOL_ENV["YDOTOOL_SOCKET"]
    if not os.path.exists(sock):
        return f"ydotool socket missing at {sock} — is ydotoold running? (systemctl status ydotool)"
    if not os.access(sock, os.W_OK):
        return f"no write access to {sock} — see the systemd override in STATUS.md"
    return None


# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------

class CaptureWorker(QThread):
    """Grabs the screen and converts each lane crop into a looming drive."""

    def __init__(self, shared, use_portal=False):
        super().__init__()
        self.shared = shared
        self.use_portal = use_portal
        self.running = True

    def _open_backend(self):
        """PipeWire portal if the user has allowed it (no popups, display
        rate), otherwise Spectacle (works unattended, but pops a notification
        per frame and writes each one to disk).

        The portal is only attempted when consent already exists or the user
        asked for it explicitly — otherwise its permission dialog would block
        startup for anyone who just wants to run the thing.
        """
        from pipewire_capture import PipeWireCapture, TOKEN_FILE

        if not (self.use_portal or os.path.exists(TOKEN_FILE)):
            print("capture: Spectacle (expect a notification per frame)")
            print("         run once with --portal to switch to the silent,")
            print("         full-speed PipeWire path; consent is remembered after.")
            return self._spectacle_backend()

        try:
            pw = PipeWireCapture()
            for _ in range(100):
                if pw.latest() is not None:
                    print("capture: PipeWire portal")
                    self.shared["backend"] = "pipewire"
                    return lambda: pw.latest()
                time.sleep(0.05)
            pw.close()
            print("capture: portal gave no frames, falling back to Spectacle")
        except Exception as exc:
            print(f"capture: portal unavailable ({exc}), falling back to Spectacle")

        return self._spectacle_backend()

    def _spectacle_backend(self):
        if not ensure_spectacle():
            raise RuntimeError("no capture backend: portal refused and Spectacle would not start")
        from screen_capture import ScreenCapture
        cap = ScreenCapture()
        self.shared["backend"] = "spectacle"
        return lambda: np.asarray(cap.grab().convert("RGB"), dtype=np.uint8)

    def run(self):
        grab = self._open_backend()
        baseline = {}
        row_weight = {}
        lanes = dict(LANES)
        calibrated_at = 0.0
        quiet_since = time.time()
        last_frame = None

        while self.running:
            try:
                frame = grab()
            except Exception as exc:
                self.shared["status"] = f"capture failed: {exc}"
                time.sleep(1.0)
                continue
            if frame is None or frame is last_frame:
                time.sleep(0.008)   # portal hands back the same buffer until a new one lands
                continue

            # Re-find the strumline at startup, and again whenever every lane
            # has been silent for a while — that usually means the window moved
            # rather than that the fly is between notes.
            now = time.time()
            if now - calibrated_at > 2.0 and (calibrated_at == 0.0 or now - quiet_since > 10.0):
                found, info = autocalibrate(Image.fromarray(frame))
                calibrated_at = now
                quiet_since = now
                if found:
                    lanes = found
                    baseline.clear()
                    row_weight.clear()
                    self.shared["lanes_info"] = info
                    print(f"lanes located: {info}")
                else:
                    self.shared["lanes_info"] = f"using saved lanes ({info})"
                    print(f"auto-calibration failed ({info}); using saved lanes from lanes.py")

            h, w = frame.shape[:2]
            for name, (x0, y0, x1, y1) in lanes.items():
                if x1 > w or y1 > h:
                    continue
                arr = frame[y0:y1, x0:x1].astype(np.float32)
                # A note sliding into the lane changes the pixels; the idle
                # strumline does not. Compare against a slow-moving background
                # estimate so the static arrow itself contributes nothing.
                # ponytail: crude vs. matching the mod's real note sprites, but
                # it needs no per-skin calibration. Upgrade if a busy stage
                # background starts triggering it.
                if name not in baseline:
                    baseline[name] = arr
                delta = np.abs(arr - baseline[name]).mean(axis=(1, 2))   # per row
                baseline[name] = 0.85 * baseline[name] + 0.15 * arr

                # A flat mean over the whole 170px crop rated a note that had
                # only just entered the lane exactly as urgent as one sitting on
                # the strum arrow, so the brain crossed threshold while the note
                # was still a third of a second away and pressed far too early.
                # Weight rows by nearness to the arrow, squared — which is also
                # how a looming object's angular size actually grows.
                if row_weight.get(name) is None or len(row_weight[name]) != len(delta):
                    row_weight[name] = np.linspace(0.0, 1.0, len(delta), dtype=np.float32) ** 2
                weight = row_weight[name]
                diff = float((delta * weight).sum() / weight.sum())
                self.shared["drive"][name] = float(np.clip(diff / 22.0, 0.0, 1.0) * DRIVE_SCALE)

                # Judging asks a different question than the brain does: not
                # "is a note coming" but "is one over the arrow right now", so
                # it reads only the band the arrow occupies.
                band = max(1, min(len(delta), x1 - x0))
                self.shared["hit"][name] = float(
                    np.clip(delta[-band:].mean() / 22.0, 0.0, 1.0) * DRIVE_SCALE)
            if max(self.shared["drive"].values()) > 0.01:
                quiet_since = time.time()
            self.shared["status"] = "live"
            last_frame = frame
            time.sleep(0.004)   # the portal delivers faster than we need to look


def demo_signals(t):
    """Synthetic chart: a note every ~0.9 s, one lane at a time.

    Modelled the way capture now reads a real one — looming drive growing
    with the square of how near the note is, plus a short pulse marking the
    moment it is actually over the arrow and meant to be hit.

    Module-level on purpose: the calibration harness (`tune.py`) drives the
    same chart through the same tick(), so a value tuned there means the same
    thing here. simulate_song.py once grew its own private press rule and the
    offline numbers stopped describing the live fly.
    """
    period, arrive = 0.9, 0.94
    idx = int(t / period)
    phase = (t % period) / period
    lane = LANE_NAMES[(idx * 7 + idx // 4) % 4]
    drive = {n: 0.0 for n in LANE_NAMES}
    hit = {n: 0.0 for n in LANE_NAMES}
    if 0.45 < phase <= arrive:
        nearness = (phase - 0.45) / (arrive - 0.45)
        drive[lane] = DRIVE_SCALE * nearness ** 2
    if abs(phase - arrive) < 0.06:
        hit[lane] = DRIVE_SCALE * (1.0 - abs(phase - arrive) / 0.06)
    return drive, hit


class SimWorker(QThread):
    """Runs the four circuit copies in real time and decides the presses."""

    state = Signal(object, dict)

    def __init__(self, shared, demo=False, dry_run=False):
        super().__init__()
        self.shared = shared
        self.demo = demo
        self.dry_run = dry_run
        self.running = True
        self.brains = {n: FlyBrain() for n in LANE_NAMES}
        self.rate_hz = {n: 0.0 for n in LANE_NAMES}
        self.gf_hz = 0.0
        self.last_press = {n: -1e9 for n in LANE_NAMES}   # no cooldown on the first note
        self.prev_rate = {n: 0.0 for n in LANE_NAMES}
        self.falling = {n: 0 for n in LANE_NAMES}
        self.crest_ready = {n: True for n in LANE_NAMES}
        self.trough = {n: 0.0 for n in LANE_NAMES}
        self.pressed = None
        self._procs = []
        self.judge = Judge(LANE_NAMES, scale=DRIVE_SCALE)
        # Injection starts disarmed: the fly shares one real keyboard with you,
        # and it used to grab it the moment the process came up. F9 arms it.
        self.armed = False
        self.stim_on = True  # demo only: 'S' pauses the synthetic notes
        self.gf_fired = False
        # Set on the first tick, not here: the live loop ticks in wall-clock
        # time but tune.py ticks from t=0, and seeding this from time.time()
        # made idle_s wildly negative there — the penalty silently switched
        # itself off in the bench, so the bench stopped describing the fly.
        self.idle_since = None
        self.alpha = (1.0 / SIM_HZ) / RATE_TAU

    def brain_meta(self):
        b = self.brains[LANE_NAMES[0]]
        roles = np.full(b.n, 3, dtype=np.int32)
        roles[b.input_idx] = 0
        roles[b.output_idx] = 1
        roles[b.escape_idx] = 2
        return {"pos": b.pos, "roles": roles}

    def _press(self, lane):
        if self.dry_run or not self.armed:
            return
        code = KEYCODES[lane]
        self._procs = [p for p in self._procs if p.poll() is None][-8:]
        self._procs.append(subprocess.Popen(
            ["ydotool", "key", f"{code}:1", f"{code}:0"],
            env=YDOTOOL_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    def tick(self, now, drive, hit):
        """One simulation step: advance the four brains, decide a press, grade.

        The whole decision lives here and nowhere else, so `tune.py` can drive
        it in simulated time and have its numbers mean something about the fly
        that actually plays.
        """
        alpha = self.alpha
        spikes_union = None
        for name in LANE_NAMES:
            b = self.brains[name]
            b.stimulate_input(drive.get(name, 0.0))
            s = b.step()
            inst = s[b.output_idx].mean() * SIM_HZ
            self.rate_hz[name] += alpha * (inst - self.rate_hz[name])
            if name == self.pressed or spikes_union is None:
                spikes_union = s
        # giant fiber across all four copies — whichever lane is closest to
        # bolting is the honest reading, not lane 0's private opinion
        gf_inst = max(b.rate(b.escape_idx) for b in self.brains.values()) * SIM_HZ
        self.gf_hz += alpha * (gf_inst - self.gf_hz)

        # Fire on the crest, not on a level. A fixed threshold ties *when* she
        # presses to *how hard* the lane pushes: a stronger signal crosses the
        # same line earlier, so she pressed 105 ms early on the demo chart and
        # tuning the line to fix that put her one bad frame away from crossing
        # it never (a 15% weaker signal missed all 26 notes). The moment the
        # firing rate stops climbing is the circuit's own answer to "it is here
        # now", and that moment does not move when the signal gets louder.
        # ponytail: FALL_STEPS is a fixed 15 ms of "yes it is really falling";
        # a two-EMA slope would ride out noise better if a busy stage ever
        # makes the rate jitter enough to trip it early.
        best, best_rate = None, 0.0
        for name in LANE_NAMES:
            r = self.rate_hz[name]

            if r < ARM_HZ:          # lane resting: forget everything about the last note
                self.prev_rate[name] = r
                self.falling[name] = 0
                self.crest_ready[name] = True
                continue

            self.falling[name] = self.falling[name] + 1 if r < self.prev_rate[name] else 0
            self.prev_rate[name] = r

            if not self.crest_ready[name]:
                # One burst, one press. The tail of a note keeps falling long
                # after the crest, so without this the same note fires again
                # every time the cooldown lapses — 26 notes were producing 36
                # presses, the extras all landing as ghost taps. Re-arm only
                # when the lane climbs back out of its own trough: that is a
                # new note looming, not this one still fading.
                self.trough[name] = min(self.trough[name], r)
                if r > self.trough[name] * REARM_RISE:
                    self.crest_ready[name] = True
                continue

            if self.falling[name] >= FALL_STEPS and r > best_rate:
                best, best_rate = name, r

        self.gf_fired = False
        if best is not None and now - self.last_press[best] > PRESS_COOLDOWN_S:
            self.last_press[best] = now
            self.crest_ready[best] = False
            self.trough[best] = best_rate
            self.pressed = best
            self.gf_fired = True
            self.idle_since = now
            self.judge.press(best, now)
            self._press(best)
        elif self.pressed and now - self.last_press[self.pressed] > 0.12:
            self.pressed = None

        # Standing still should feel bad too, not just missing a note: nothing
        # above ever fires this section if she just never reacts to anything,
        # so a genuinely frozen fly would otherwise sit at a flat, comfortable
        # mood forever. Ramps up exponentially past a grace period so a normal
        # gap between notes costs nothing, but real paralysis compounds.
        if self.idle_since is None:
            self.idle_since = now
        if self.armed or self.demo:
            idle_s = now - self.idle_since
            if idle_s > IDLE_GRACE_S:
                over = idle_s - IDLE_GRACE_S
                pain_per_s = IDLE_PAIN_PEAK * (np.exp(over / IDLE_PAIN_TAU) - 1.0)
                for b in self.brains.values():
                    b.apply_judgment(-pain_per_s / SIM_HZ)
        else:
            self.idle_since = now

        # Dopamine and pain, from how close each press landed to the note
        # the brain was reacting to — the other thing that moves mood.
        events = self.judge.update(now, hit)
        for event in events:
            for b in self.brains.values():
                b.apply_judgment(event.reward)
        return spikes_union, events

    def snapshot(self, sim_rate, hit):
        return {
            "lane_hz": dict(self.rate_hz),
            "pressed": self.pressed,
            "gf_hz": self.gf_hz,
            "gf_fired": self.gf_fired,
            "total_spikes": sum(b.total_spikes for b in self.brains.values()),
            "mood": self.brains[LANE_NAMES[0]].mood,
            "status": "demo" if self.demo else self.shared.get("status", "live"),
            "fps": sim_rate,
            "score": self.judge.score,
            "combo": self.judge.combo,
            "judgment": self.judge.last,
            "accuracy": self.judge.accuracy_pct,
            "armed": self.armed or self.dry_run or self.demo,
            "stim_on": self.stim_on,
            # what the judge thinks is sitting on the arrow right now, so the
            # overlay can show the fly's own read of "this one is due"
            "note_here": {n: hit.get(n, 0.0) / DRIVE_SCALE for n in LANE_NAMES},
        }

    def run(self):
        dt = 1.0 / SIM_HZ
        emit_every = SIM_HZ // EMIT_HZ
        t_start = time.time()
        next_step = t_start
        step_i = 0
        sim_rate = 0.0
        last_rate_check = t_start
        steps_since = 0

        while self.running:
            now = time.time()
            if now < next_step:
                time.sleep(min(0.002, next_step - now))
                continue
            next_step += dt
            if now - next_step > 0.25:         # fell behind badly; resync
                next_step = now + dt

            if self.demo:
                drive, hit = (demo_signals(now - t_start) if self.stim_on
                              else (ZERO_LANES, ZERO_LANES))
            else:
                drive, hit = self.shared["drive"], self.shared["hit"]

            spikes_union, _ = self.tick(now, drive, hit)

            steps_since += 1
            if now - last_rate_check >= 0.5:
                sim_rate = steps_since / (now - last_rate_check)
                steps_since = 0
                last_rate_check = now

            step_i += 1
            if step_i % emit_every == 0:
                self.state.emit(spikes_union, self.snapshot(sim_rate, hit))


def main():
    demo = "--demo" in sys.argv
    dry_run = "--dry-run" in sys.argv

    if not demo:
        if not dry_run:
            problem = check_ydotool()
            if problem:
                sys.exit(problem)
        print("live mode — put the FNF tab in front and start a song.")
        print("the lanes are located automatically, and re-located if they go quiet.")
    else:
        print("demo mode — synthetic notes, no game or screen capture needed.")

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # XWayland: KWin honours stays-on-top there
    app = QApplication(sys.argv)

    # Python's default SIGINT handler only fires between interpreter
    # bytecodes, so Ctrl+C used to land mid-paintEvent (skipping p.end())
    # and spam painter errors forever. SIG_DFL kills the process at the OS
    # level the instant the signal arrives, no interpreter coordination
    # needed, so it can't land mid-callback.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    shared = {"drive": {n: 0.0 for n in LANE_NAMES},
              "hit": {n: 0.0 for n in LANE_NAMES},
              "status": "starting"}
    sim = SimWorker(shared, demo=demo, dry_run=dry_run)
    win = Overlay(sim.brain_meta(), demo=demo)
    sim.state.connect(win.push)
    if demo:
        def toggle_stim():
            sim.stim_on = not sim.stim_on
            print("stimulus ON" if sim.stim_on else "stimulus OFF — brain idling")
        win.stim_toggle.connect(toggle_stim)

    screen = app.primaryScreen().availableGeometry()
    win.move(screen.right() - win.width() - 24, screen.top() + 24)
    win.show()

    sim.start()
    capture = None
    if not demo:
        capture = CaptureWorker(shared, use_portal="--portal" in sys.argv)
        capture.start()

    watch = None
    if not demo and not dry_run:
        watch = arm_switch(sim)

    try:
        app.exec()
    finally:
        sim.running = False
        if capture:
            capture.running = False
        if watch:
            watch.running = False
        sim.wait(1000)


def arm_switch(sim):
    """F9 arms and disarms the fly; any other key disarms it.

    A Qt shortcut cannot do this — it only fires while the overlay has focus,
    and the whole point is to take the keyboard back while the *game* has it.
    So we read the evdev stream directly. Nothing is logged or stored: the
    watcher only ever asks "was that F9, or was it something else".
    """
    def toggle():
        sim.armed = not sim.armed
        print("fly ARMED — it has the keyboard" if sim.armed
              else "fly disarmed — keyboard is yours")

    def stand_down():
        if sim.armed:
            sim.armed = False
            print("fly disarmed — you touched the keyboard")

    watch = KeyboardWatch(on_toggle=toggle, on_other_key=stand_down)
    if watch.error:
        # Without the watcher there is no way to arm it at all, so fall back to
        # the old always-on behaviour rather than shipping a fly that can never play.
        sim.armed = True
        print(f"WARNING: {watch.error}")
        print("         starting ARMED; kill the process to stop it.")
        return None

    watch.start()
    print(f"\n  the fly starts DISARMED — press F9 (key {TOGGLE_KEY}) to hand it the keyboard.")
    print("  press F9 again, or just type anything, and it lets go instantly.\n")
    return watch


if __name__ == "__main__":
    main()
