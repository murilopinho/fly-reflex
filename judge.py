"""Turns what actually happened into dopamine, pain and points.

FNF grades a hit by how close the key was to the moment the note reached the
strumline, so that is what we measure. The capture thread already reports, per
lane, how strongly something is moving through the hit window; a note is the
rise and fall of that signal, and its peak is the moment it was meant to be
hit. The sim reports when it pressed. The gap between the two is the timing
error, and every number below comes out of it.

Reward is a continuous function of that error — a note nailed dead on is worth
the full dopamine hit, one scraped in at the edge of the window is worth a
sliver of it, and a note that goes by untouched is a miss at -1. Deliberately
not a lookup table of fixed Sick/Good/Bad values: the question this module
exists to answer is whether the fly's reward tracks how well it actually
played, and a table of constants per bucket hides exactly that. The tier names
are printed for the overlay only — nothing downstream reads them.

Reading FNF's own judgment text would be better still, but that needs OCR of
the score line; see STATUS.md.
"""

HIT_WINDOW_S = 0.18      # how far off the peak a press can be and still count
GRACE_S = 0.12           # an early press waits this long for its note to show up
MAX_NOTE_S = 1.2         # a lane stuck bright is not a note; give up on it
ON, OFF = 0.35, 0.20     # note enters / leaves the hit window (fraction of peak drive)

MAX_DOPAMINE = 0.6       # reward for a perfect hit, same scale as before
MISS_PAIN = -1.0
GHOST_PAIN = -0.15       # pressed with no note anywhere near

SCORE_MAX, SCORE_MIN = 350, 50
MISS_PENALTY = 100


def tier(accuracy):
    """Display name only — the reward is the continuous value, not this."""
    if accuracy >= 0.75:
        return "Sick"
    if accuracy >= 0.45:
        return "Good"
    if accuracy >= 0.20:
        return "Bad"
    return "Shit"


class Event:
    __slots__ = ("lane", "name", "reward", "points", "error", "accuracy", "delta")

    def __init__(self, lane, name, reward, points, error, accuracy, delta=0.0):
        self.lane, self.name = lane, name
        self.reward, self.points = reward, points
        self.error, self.accuracy = error, accuracy
        # signed: negative = pressed before the note was over the arrow. `error`
        # alone cannot tell early from late, and which way she is off is the
        # whole question when tuning the press threshold.
        self.delta = delta

    def __repr__(self):
        return (f"<{self.lane} {self.name} err={self.error:.3f}s "
                f"acc={self.accuracy:.2f} reward={self.reward:+.3f} pts={self.points:+d}>")


class _Note:
    __slots__ = ("t_enter", "t_peak", "peak", "t_press", "departed")

    def __init__(self, t, drive):
        self.t_enter = self.t_peak = t
        self.peak = drive
        self.t_press = None
        self.departed = False


class Judge:
    """Per-lane note tracking and grading. Pure logic — no screen, no Qt."""

    def __init__(self, lanes, scale=1.0):
        """`scale` is the drive value a note at full strength reaches — the
        capture path scales its output by CALIBRATED_INTENSITY, so a bare 0..1
        threshold here would never fire."""
        self.lanes = list(lanes)
        self.on_threshold = ON * scale
        self.off_threshold = OFF * scale
        self.notes = {n: None for n in self.lanes}
        self.pending_press = {n: None for n in self.lanes}
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.hits = 0
        self.misses = 0
        self.last = ""

    # -- inputs -----------------------------------------------------------

    def press(self, lane, t):
        """The sim pressed this lane. Attaches to the note in flight, or waits
        briefly for one — the brain routinely fires as a note enters the window,
        and punishing that as a ghost tap would slander a correct early hit."""
        note = self.notes.get(lane)
        if note is not None:
            if note.t_press is None:
                note.t_press = t
        elif self.pending_press[lane] is None:
            self.pending_press[lane] = t

    def update(self, t, drive):
        """Advance every lane. Returns the events that resolved this step."""
        events = []
        for lane in self.lanes:
            d = float(drive.get(lane, 0.0))
            note = self.notes[lane]

            if note is None:
                if d > self.on_threshold:
                    self._start(lane, t, d)
                else:
                    waiting = self.pending_press[lane]
                    if waiting is not None and t - waiting > GRACE_S:
                        self.pending_press[lane] = None
                        events.append(self._ghost(lane))
                continue

            if not note.departed:
                if d > note.peak:
                    note.peak, note.t_peak = d, t
                if d < self.off_threshold or t - note.t_enter > MAX_NOTE_S:
                    note.departed = True     # peak is final now; error can be measured
                continue

            # The note has left the window but the press may still be coming.
            # Resolving the instant the pixels go quiet would cap late hits at
            # however fast the sprite scrolls away — a few tens of ms — and
            # HIT_WINDOW_S would mean nothing.
            if note.t_press is not None or t > note.t_peak + HIT_WINDOW_S:
                self.notes[lane] = None
                events.append(self._resolve(lane, note))
            elif d > self.on_threshold:       # next note already here; call this one
                self.notes[lane] = None
                events.append(self._resolve(lane, note))
                self._start(lane, t, d)

        for e in events:
            self.last = e.name
        return events

    def _start(self, lane, t, drive):
        note = self.notes[lane] = _Note(t, drive)
        waiting = self.pending_press[lane]
        if waiting is not None and t - waiting <= GRACE_S:
            note.t_press = waiting              # that early press was for this note
        self.pending_press[lane] = None

    # -- grading ----------------------------------------------------------

    def _resolve(self, lane, note):
        if note.t_press is None:
            return self._miss(lane, float("inf"))
        delta = note.t_press - note.t_peak
        error = abs(delta)
        if error > HIT_WINDOW_S:
            return self._miss(lane, error, delta)

        accuracy = 1.0 - error / HIT_WINDOW_S          # 1.0 dead on, 0.0 at the edge
        reward = MAX_DOPAMINE * accuracy
        points = SCORE_MIN + int(round((SCORE_MAX - SCORE_MIN) * accuracy))
        self.score += points
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.hits += 1
        return Event(lane, tier(accuracy), reward, points, error, accuracy, delta)

    def _miss(self, lane, error, delta=float("inf")):
        self.score -= MISS_PENALTY
        self.combo = 0
        self.misses += 1
        return Event(lane, "Miss", MISS_PAIN, -MISS_PENALTY, error, 0.0, delta)

    def _ghost(self, lane):
        # FNF's own default (ghost tapping on) charges nothing for hitting air,
        # so this costs no points and does not break the combo — it only stings
        # a little, because a startle that hit nothing was still wasted. Making
        # it break the combo punished the resting brain's spontaneous twitches
        # and buried a clean 51-note run at a best combo of 3.
        return Event(lane, "Ghost", GHOST_PAIN, 0, float("inf"), 0.0)

    @property
    def accuracy_pct(self):
        total = self.hits + self.misses
        return 100.0 * self.hits / total if total else 0.0


# --------------------------------------------------------------------------


def _play_note(judge, lane, press_offset, peak_t=1.0, dt=0.005, amp=1.0):
    """Drive one synthetic note through the judge, pressing `press_offset`
    seconds away from its peak (None = never press). Returns the event."""
    out = []
    pressed = press_offset is None
    t = peak_t - 0.40
    while t < peak_t + 0.25:
        if t < peak_t:
            d = max(0.0, (t - (peak_t - 0.30)) / 0.30)      # ramp in
        else:
            d = max(0.0, 1.0 - (t - peak_t) / 0.04)          # note leaves fast
        if not pressed and t >= peak_t + press_offset:
            judge.press(lane, t)
            pressed = True
        out += judge.update(t, {lane: d * amp})
        t += dt
    return out[0] if out else None


def _demo():
    lanes = ["left", "down", "up", "right"]

    # 1. reward must fall as timing gets worse — the whole point of the module
    rewards, points = [], []
    for offset in (0.0, 0.04, 0.08, 0.12, 0.16):
        j = Judge(lanes)
        e = _play_note(j, "left", offset)
        assert e is not None, f"no event for offset {offset}"
        assert e.name != "Miss", f"offset {offset}s should still be a hit, got {e}"
        rewards.append(e.reward)
        points.append(e.points)
        print(f"  press {offset*1000:4.0f} ms off peak -> {e}")

    assert all(a > b for a, b in zip(rewards, rewards[1:])), \
        f"dopamine must shrink as timing worsens, got {rewards}"
    assert all(a > b for a, b in zip(points, points[1:])), \
        f"points must shrink as timing worsens, got {points}"
    assert len(set(rewards)) == len(rewards), "flat reward — this is the bug we are fixing"
    assert abs(rewards[0] - MAX_DOPAMINE) < 1e-6, "a perfect hit should pay full dopamine"

    # 2. a note nobody pressed is a miss: pain, lost points, broken combo
    j = Judge(lanes)
    _play_note(j, "up", 0.0)
    assert j.combo == 1 and j.score > 0
    before = j.score
    e = _play_note(j, "up", None)
    assert e.name == "Miss" and e.reward == MISS_PAIN, e
    assert j.score == before - MISS_PENALTY, "a miss has to cost points, not just mood"
    assert j.combo == 0, "a miss has to break the combo"
    print(f"  missed note        -> {e}   score {before} -> {j.score}")

    # 3. pressing early, before the note is visible, still counts as that note
    j = Judge(lanes)
    j.press("down", 0.62)
    e = _play_note(j, "down", None, peak_t=0.70)
    assert e.name != "Miss", f"an early press inside the grace window is a hit, got {e}"
    print(f"  early press        -> {e}")

    # 4. pressing at nothing at all is a ghost tap, not a free note
    j = Judge(lanes)
    _play_note(j, "right", 0.0)                 # bank a combo first
    banked = j.combo
    j.press("right", 5.0)
    events = []
    t = 5.0
    while t < 5.4:
        events += j.update(t, {"right": 0.0})
        t += 0.01
    assert len(events) == 1 and events[0].name == "Ghost", events
    assert j.combo == banked, "hitting air is free in FNF; it must not break the combo"
    print(f"  ghost tap          -> {events[0]}")

    # 5. mood must actually land differently for a good run vs a bad one
    good, bad = Judge(lanes), Judge(lanes)
    for i in range(8):
        _play_note(good, lanes[i % 4], 0.01, peak_t=1.0 + i * 2)
        _play_note(bad, lanes[i % 4], None, peak_t=1.0 + i * 2)
    assert good.score > 0 > bad.score, (good.score, bad.score)
    assert good.accuracy_pct == 100.0 and bad.accuracy_pct == 0.0
    print(f"  8 clean notes -> score {good.score:+d}, combo {good.best_combo}, "
          f"{good.accuracy_pct:.0f}% | 8 missed -> score {bad.score:+d}, "
          f"{bad.accuracy_pct:.0f}%")

    # 6. thresholds have to follow the drive scale: the live capture path caps
    # drive at CALIBRATED_INTENSITY*1.6 (~0.19), nowhere near a bare 0..1.
    live_scale = 0.12 * 1.6
    j = Judge(lanes, scale=live_scale)
    e = _play_note(j, "left", 0.0, amp=live_scale)
    assert e is not None and e.name == "Sick", f"scaled drive must still register, got {e}"
    blind = Judge(lanes)                       # scale=1.0 against the same drive
    missed_it = _play_note(blind, "left", 0.0, amp=live_scale)
    assert missed_it is not None and missed_it.name == "Ghost", \
        f"unscaled thresholds should never see the note at all, got {missed_it}"
    print(f"  live drive scale   -> {e}")
    print("OK")


if __name__ == "__main__":
    _demo()
