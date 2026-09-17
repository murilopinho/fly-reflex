"""Calibration bench: how well does she actually time her presses?

Runs the real `SimWorker.tick()` — the same code the live fly runs — against
the same synthetic chart, in simulated time, so a sweep that takes a minute
here describes the fly that plays the game. Nothing about the decision is
reimplemented; that mistake already happened once (simulate_song.py grew its
own press rule and its numbers stopped meaning anything about the live loop).

    python3 tune.py            # sweep, with the current settings marked
    python3 tune.py --events   # one run, printing every judged event

What to read: `delta` is signed, in milliseconds, and it is the whole point.
Negative means she pressed BEFORE the note reached the arrow. Which side she
errs on decides which knob to turn — and the two knobs push in opposite
directions, so tuning by "accuracy went up" alone will eventually walk the
wrong one.
"""
import sys

import run
from judge import HIT_WINDOW_S
from overlay import LANE_NAMES

TIERS = ["Sick", "Good", "Bad", "Shit", "Miss", "Ghost"]
CURRENT_TAU, CURRENT_FALL = run.RATE_TAU, run.FALL_S
SECONDS = 24.0          # 26 notes at the demo chart's 0.9 s period


def measure(rate_tau=None, fall_s=None, seconds=SECONDS, amp=1.0):
    """Run the real tick() in simulated time; return the judged events.

    `amp` scales the looming drive only — the judge still sees the note at
    full strength. That is the real game's variable: a dimmer note skin, a
    faster scroll or a busier stage all change how hard the lane pushes the
    circuit, without changing when the note is actually due. A press rule that
    only works at amp=1.0 is a rule tuned to this chart, not to the game.
    """
    saved = run.FALL_S, run.RATE_TAU  # module globals are read inside tick(); putting
    if fall_s is not None:            # them back keeps one sweep from poisoning the next
        run.FALL_S = fall_s           # (it silently did, and the amplitude block lied)
    shared = {"drive": dict(run.ZERO_LANES), "hit": dict(run.ZERO_LANES), "status": "tune"}
    sim = run.SimWorker(shared, demo=True, dry_run=True)   # dry_run: never touches ydotool
    if rate_tau is not None:
        run.RATE_TAU = rate_tau       # tick() reads it per step now, not sim.alpha once

    events = []
    dt = 1.0 / run.SIM_HZ
    for i in range(int(seconds * run.SIM_HZ)):
        t = i * dt
        px, hit_px = run.demo_pixels(t)
        if amp != 1.0:
            px = {k: v * amp for k, v in px.items()}
        drive = {k: run.pixels_to_drive(v) for k, v in px.items()}
        hit = {k: run.pixels_to_drive(v) for k, v in hit_px.items()}
        _, evs = sim.tick(t, drive, hit)
        events.extend(evs)
    run.FALL_S, run.RATE_TAU = saved
    return sim, events


def summarize(events):
    counts = {name: 0 for name in TIERS}
    deltas = []
    for e in events:
        counts[e.name] = counts.get(e.name, 0) + 1
        if e.name not in ("Miss", "Ghost"):
            deltas.append(e.delta)
    mean_delta = sum(deltas) / len(deltas) if deltas else float("nan")
    return counts, mean_delta


def line(label, counts, mean_delta):
    graded = sum(counts[t] for t in ("Sick", "Good", "Bad", "Shit"))
    cells = " ".join(f"{t[:4]}{counts[t]:>3}" for t in TIERS)
    return (f"{label:<22} {cells}   delta {mean_delta * 1000:+6.0f} ms"
            f"   hits {graded}")


def sweep():
    print(f"chart: one note every 0.9 s over {SECONDS:.0f} s · hit window "
          f"±{HIT_WINDOW_S * 1000:.0f} ms · Sick needs |delta| <= "
          f"{HIT_WINDOW_S * 0.25 * 1000:.0f} ms\n")

    print("-- RATE_TAU (how much the rate is smoothed before the crest shows) --")
    for tau in (0.04, 0.08, 0.12, 0.16, 0.20):
        counts, md = measure_and_sum(tau, run.FALL_S)
        mark = "  <- current" if tau == CURRENT_TAU else ""
        print(line(f"RATE_TAU {tau:.2f}", counts, md) + mark)

    print("\n-- FALL_S (ms of falling rate before it counts as a crest) --")
    for fs in (0.005, 0.010, 0.015, 0.020, 0.030):
        counts, md = measure_and_sum(run.RATE_TAU, fs)
        mark = "  <- current" if fs == CURRENT_FALL else ""
        print(line(f"FALL_S {fs*1000:.0f}ms", counts, md) + mark)

    print("\n-- signal strength: the test a fixed threshold could not pass --")
    for amp in (0.7, 0.85, 1.0, 1.15, 1.3):
        counts, md = measure_and_sum(run.RATE_TAU, run.FALL_S, amp=amp)
        print(line(f"drive x{amp:.2f}", counts, md))


def measure_and_sum(tau, fall_steps=None, amp=1.0):
    _, events = measure(tau, fall_steps, amp=amp)
    return summarize(events)


def show_events():
    sim, events = measure()
    print(f"{'lane':<6} {'tier':<6} {'delta ms':>9} {'reward':>7} {'combo after':>12}")
    for e in events:
        d = "  --" if e.delta in (float("inf"), float("-inf")) else f"{e.delta * 1000:+.0f}"
        print(f"{e.lane:<6} {e.name:<6} {d:>9} {e.reward:>+7.2f}")
    counts, md = summarize(events)
    print("\n" + line("TOTAL", counts, md))
    print(f"score {sim.judge.score}, best combo {sim.judge.best_combo}, "
          f"accuracy {sim.judge.accuracy_pct:.0f}%")


def _demo():
    """Self-check: the bench must actually grade notes, the signed delta must
    survive, and — the whole reason the press rule changed — the timing must
    not move much when the drive gets louder or quieter. A level threshold
    failed that last one badly: 15% quieter and it missed all 26 notes."""
    _, events = measure(seconds=6.0)
    assert events, "no events in 6 s — the chart or the press rule stopped working"
    counts, md = summarize(events)
    assert md == md, "mean delta is nan — no gradeable press landed"
    assert abs(md) < 1.0, f"delta of {md:.2f}s is bigger than the whole chart period"
    print(line("selfcheck 6 s", counts, md))

    spread = []
    for amp in (0.7, 1.0, 1.3):
        _, evs = measure(seconds=6.0, amp=amp)
        c, d = summarize(evs)
        assert c["Miss"] == 0, f"drive x{amp} missed {c['Miss']} notes — amplitude still decides"
        spread.append(d)
        print(line(f"  drive x{amp:.1f}", c, d))
    drift = max(spread) - min(spread)
    assert drift < HIT_WINDOW_S / 2, f"timing moved {drift*1000:.0f} ms across a 2x drive swing"
    print(f"timing drift across a 0.7x..1.3x drive swing: {drift*1000:.0f} ms")
    print("OK")


if __name__ == "__main__":
    if "--events" in sys.argv:
        show_events()
    elif "--selfcheck" in sys.argv:
        _demo()
    else:
        sweep()
