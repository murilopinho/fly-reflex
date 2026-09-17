"""Run the real FlyWire circuit against a synthetic FNF-style note chart and
export a frame-by-frame recording (spikes, mood, decisions) as JSON for the
visualization artifact.

No live game or browser needed — this is the offline "does the circuit react
sensibly" demo. The note chart is made up (random lane, random timing), but how
a hit is *graded* is not: it goes through the same judge.py the live game uses,
so dopamine and points here track the fly's real timing instead of being drawn
from a table of fixed values per bucket, which is what this file used to do.

Design note on lanes: the real circuit.json only has ONE pooled population of
looming-detector inputs (LPLC2/LC4) and ONE pooled population of escape
outputs (DN*/MDN) — it doesn't structurally know "left" from "right". To get
a 4-lane decision out of a single circuit, we run 4 independent copies that
share the same synaptic weights (real, fixed) but keep separate membrane
potential per lane, each driven only by that lane's note proximity. Whichever
copy's output layer fires hardest wins the keypress. That's the same trick
the live pipeline will need later.
"""
import json
import random

import numpy as np

from fly_brain import FlyBrain, CALIBRATED_INTENSITY, MOOD_DECAY
from judge import Judge

LANE_NAMES = ["left", "down", "up", "right"]
STEPS_PER_SECOND = 30
SONG_SECONDS = 24
APPROACH_STEPS = 12  # how many steps a note "looms" before it must be hit
DRIVE_SCALE = CALIBRATED_INTENSITY * 1.6
PRESS_COOLDOWN_S = 0.18

# MOOD_DECAY is a per-step constant calibrated for the 1 kHz brain loop. This
# file steps at 30 Hz, so applying it raw made a mood here last thirty times
# longer than the same mood in the live fly — the hormone outlived the song.
MOOD_DECAY_PER_STEP = MOOD_DECAY ** (1000.0 / STEPS_PER_SECOND)


def make_chart(rng, n_steps):
    """Random note chart: list of (hit_step, lane_index)."""
    chart = []
    t = 20
    while t < n_steps - 2:
        chart.append((t, rng.randrange(4)))
        t += rng.randint(8, 22)
    return chart


def run():
    rng = random.Random(7)
    n_steps = STEPS_PER_SECOND * SONG_SECONDS
    chart = make_chart(rng, n_steps)

    # 4 independent lane instances sharing the same real synaptic weights
    lanes = [FlyBrain("desktop-fly/data/circuit.json") for _ in range(4)]
    mood = 0.0

    # sample a fixed subset of neurons for the raster plot (visual clarity)
    ref = lanes[0]
    rng2 = np.random.default_rng(0)
    input_sample = rng2.choice(ref.input_idx, size=min(40, len(ref.input_idx)), replace=False)
    output_all = ref.output_idx
    raster_neurons = np.concatenate([input_sample, output_all])
    raster_kind = ["input"] * len(input_sample) + ["output"] * len(output_all)

    frames = []
    upcoming = list(chart)
    active_notes = []  # (hit_step, lane)
    judge = Judge(LANE_NAMES, scale=DRIVE_SCALE)
    last_press_t = {n: -99.0 for n in LANE_NAMES}
    _rewards_seen = []

    for step in range(n_steps):
        while upcoming and upcoming[0][0] - APPROACH_STEPS <= step:
            active_notes.append(upcoming.pop(0))

        t = step / STEPS_PER_SECOND
        lane_intensity = [0.0, 0.0, 0.0, 0.0]
        lane_hit = [0.0, 0.0, 0.0, 0.0]
        still_active = []
        for hit_step, lane in active_notes:
            dist = hit_step - step
            if dist < -APPROACH_STEPS // 2:
                continue                      # long gone; the judge already called it
            # Looming grows with the square of nearness, matching what the live
            # capture path measures now.
            proximity = 1.0 - min(1.0, max(0, dist) / APPROACH_STEPS)
            lane_intensity[lane] = max(lane_intensity[lane], DRIVE_SCALE * proximity ** 2)
            # ...and a separate pulse for the note actually sitting on the line.
            over_line = max(0.0, 1.0 - abs(dist) / 2.0)
            lane_hit[lane] = max(lane_hit[lane], DRIVE_SCALE * over_line)
            still_active.append((hit_step, lane))
        active_notes = still_active

        spikes_per_lane = []
        for i, brain in enumerate(lanes):
            brain.mood = mood
            brain.stimulate_input(lane_intensity[i])
            s = brain.step()
            spikes_per_lane.append(s)

        output_rates = [s[output_all].mean() for s in spikes_per_lane]
        pressed = int(np.argmax(output_rates)) if max(output_rates) > 0.03 else None

        if pressed is not None and t - last_press_t[LANE_NAMES[pressed]] > PRESS_COOLDOWN_S:
            last_press_t[LANE_NAMES[pressed]] = t
            judge.press(LANE_NAMES[pressed], t)

        # Same grading as the live fly: reward is a continuous function of how
        # far the press landed from the note, not a value picked out of a table.
        for event in judge.update(t, dict(zip(LANE_NAMES, lane_hit))):
            mood = float(np.clip(mood + event.reward, -1.0, 1.0))
            _rewards_seen.append(event.reward)

        mood *= MOOD_DECAY_PER_STEP  # song-level mood is separate from each brain's copy

        raster = spikes_per_lane[pressed if pressed is not None else 0][raster_neurons].astype(int).tolist()

        frames.append({
            "t": step / STEPS_PER_SECOND,
            "raster": raster,
            "lane_rates": [round(r, 4) for r in output_rates],
            "pressed": LANE_NAMES[pressed] if pressed is not None else None,
            "active_lanes": [LANE_NAMES[l] for _, l in active_notes],
            "mood": round(mood, 3),
            "combo": judge.combo,
            "score": judge.score,
            "judgment": judge.last,
        })

    out = {
        "steps_per_second": STEPS_PER_SECOND,
        "raster_kind": raster_kind,
        "n_neurons_total": ref.n,
        "n_synapses": int((ref.W != 0).sum()),
        "frames": frames,
    }
    with open("song_recording.json", "w") as f:
        json.dump(out, f)
    print(f"wrote {len(frames)} frames, {len(chart)} notes, song_recording.json")
    print(f"  hits {judge.hits} / misses {judge.misses} "
          f"({judge.accuracy_pct:.0f}%), score {judge.score}, best combo {judge.best_combo}")

    rewards = sorted({round(e, 3) for e in _rewards_seen})
    assert len(rewards) > 2, f"reward collapsed to {rewards} — grading is flat again"
    print(f"  distinct dopamine values this run: {rewards}")


if __name__ == "__main__":
    run()
