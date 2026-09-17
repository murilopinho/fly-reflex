"""LIF simulation of the real FlyWire circuit (data/circuit.json from desktop-fly).

668 real neurons, ~19k real signed synaptic weights, real 3D soma positions.

Parameters follow the reference implementation's documented operating point
(desktop-fly/CLAUDE.md): 1 kHz steps, tau 20.4 ms, threshold 1.0, weight scale
0.0008/synapse, 2 ms refractory, and a baseline drive that rests neurons just
under threshold so the network has spontaneous activity instead of sitting
dead silent. That resting point is razor-thin by design — never scale the
baseline linearly or whole populations go quiet.

Populations that matter here:
  LPLC2 (210) + LC4 (104)  looming detectors — the visual input layer
  DNp01 (2)                giant fiber — a spike means "escape NOW" (the press)
  DNa01/DNa02 (2+2)        steering, as a left-minus-right rate difference
  DNp09, DNg11, MDN, ...   walk / groom / backward drives (unused for FNF)
"""
import json
import os

import numpy as np

DT = 1.0             # ms per step (1 kHz, same as the reference sim)
TAU = 20.4           # membrane time constant (ms)
V_THRESH = 1.0
V_RESET = 0.0
REFRACTORY_MS = 2.0
SYN_SCALE = 0.0008   # current per synapse (signed: + excitatory, - inhibitory)
BASELINE = 0.036     # tonic drive; rests neurons at BASELINE*TAU ≈ 0.73, just under threshold
NOISE = 0.030        # per-step current jitter — what makes the resting brain flicker.
                     # Swept: 0.02 leaves the network dead silent, 0.05+ drowns the
                     # signal. 0.03 gives ~0.7 Hz at rest vs ~17 Hz driven (25x contrast).
MOOD_DECAY = 0.998   # per-step decay toward neutral mood (at 1 kHz)
MOOD_GAIN = 0.15     # how much mood shifts the output layer's firing threshold

# Looming drive added on top of baseline when a note is bearing down on a lane.
# Calibrated by sweep (see calibrate() below): under ~0.05 nothing reaches the
# descending layer, over ~0.30 it saturates and mood stops mattering.
CALIBRATED_INTENSITY = 0.12

INPUT_TYPES = ("LPLC2", "LC4")
ESCAPE_TYPE = "DNp01"          # giant fiber
STEER_TYPES = ("DNa01", "DNa02")


class FlyBrain:
    def __init__(self, circuit_path=None):
        if circuit_path is None:
            circuit_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "desktop-fly", "data", "circuit.json"
            )
        d = json.load(open(circuit_path))
        self.neurons = d["neurons"]
        n = len(self.neurons)
        self.n = n
        self.type_of = [ne["type"] for ne in self.neurons]
        self.side_of = [ne.get("side") for ne in self.neurons]
        self.pos = np.array([ne["pos"] for ne in self.neurons], dtype=np.float32)

        self.W = np.zeros((n, n), dtype=np.float32)
        for src, tgt, w in d["edges"]:
            self.W[src, tgt] += w * SYN_SCALE

        def idx_of(pred):
            return np.array([i for i, t in enumerate(self.type_of) if pred(t)], dtype=np.int32)

        self.input_idx = idx_of(lambda t: t in INPUT_TYPES)
        self.output_idx = idx_of(lambda t: t.startswith("DN") or t == "MDN")
        self.escape_idx = idx_of(lambda t: t == ESCAPE_TYPE)
        self.steer_left_idx = np.array(
            [i for i, t in enumerate(self.type_of)
             if t in STEER_TYPES and self.side_of[i] == "left"], dtype=np.int32)
        self.steer_right_idx = np.array(
            [i for i, t in enumerate(self.type_of)
             if t in STEER_TYPES and self.side_of[i] == "right"], dtype=np.int32)

        self.rng = np.random.default_rng()
        self.reset()

    def reset(self):
        self.v = np.full(self.n, BASELINE * TAU, dtype=np.float32)
        self.spikes = np.zeros(self.n, dtype=bool)
        self.refractory = np.zeros(self.n, dtype=np.float32)
        self.ext_input = np.zeros(self.n, dtype=np.float32)
        self.mood = 0.0
        self.total_spikes = 0

    def stimulate_input(self, intensity):
        """Looming drive onto LPLC2/LC4. Scalar, or an array over input_idx."""
        self.ext_input[:] = 0.0
        self.ext_input[self.input_idx] = intensity

    def apply_judgment(self, r):
        """r>0 dopamine (Good/Sick), r<0 pain (Miss/Shit/Bad)."""
        self.mood = float(np.clip(self.mood + r, -1.0, 1.0))

    def step(self):
        # Only a handful of neurons spike per step, so summing those rows beats
        # a full 668x668 matmul by ~30x — that is what buys us real-time 1 kHz.
        fired = np.flatnonzero(self.spikes)
        syn_input = self.W[fired].sum(axis=0) if fired.size else 0.0
        noise = self.rng.normal(0.0, NOISE, self.n).astype(np.float32)
        i_total = BASELINE + self.ext_input + syn_input + noise

        thresh = np.full(self.n, V_THRESH, dtype=np.float32)
        thresh[self.output_idx] -= MOOD_GAIN * self.mood  # confident when mood>0, hesitant when <0

        self.v += (DT / TAU) * (-self.v + i_total * TAU)

        free = self.refractory <= 0.0
        self.spikes = free & (self.v > thresh)
        self.v[self.spikes] = V_RESET
        self.refractory[self.spikes] = REFRACTORY_MS
        self.refractory = np.maximum(0.0, self.refractory - DT)

        self.mood *= MOOD_DECAY
        self.total_spikes += int(self.spikes.sum())
        return self.spikes

    def rate(self, idx):
        """Instantaneous firing fraction of a population this step."""
        return float(self.spikes[idx].mean()) if len(idx) else 0.0

    def output_rate(self, window_spikes):
        return float(np.mean([s[self.output_idx].mean() for s in window_spikes]))

    def turn_bias(self):
        """DNa01/DNa02 left-minus-right rate — the circuit's own steering signal."""
        return self.rate(self.steer_left_idx) - self.rate(self.steer_right_idx)


def calibrate(brain, intensities=(0.0, 0.05, 0.08, 0.12, 0.20, 0.30), steps=300):
    """Sweep looming drive vs. descending-output rate. Returns [(intensity, rate)]."""
    out = []
    for intensity in intensities:
        brain.reset()
        fired = 0
        for _ in range(steps):
            brain.stimulate_input(intensity)
            s = brain.step()
            fired += int(s[brain.output_idx].sum())
        out.append((intensity, fired / (steps * len(brain.output_idx))))
    return out


def _demo():
    brain = FlyBrain()
    print(f"neurons={brain.n}  looming inputs={len(brain.input_idx)}  "
          f"descending outputs={len(brain.output_idx)}  giant fiber={len(brain.escape_idx)}")

    sweep = calibrate(brain)
    for intensity, rate in sweep:
        print(f"  looming drive {intensity:.2f} -> descending rate {rate:.4f}")

    rest_rate, driven_rate = sweep[0][1], sweep[3][1]
    assert driven_rate > rest_rate, "looming input must raise descending-neuron activity"

    # the brain must be alive at rest, not silent — that is what baseline+noise buys
    brain.reset()
    for _ in range(200):
        brain.step()
    assert brain.total_spikes > 0, "resting network should have spontaneous activity"

    # mood check at the calibrated operating point
    def rate_with_mood(mood_val):
        brain.reset()
        brain.mood = mood_val
        fired = 0
        for _ in range(400):
            brain.stimulate_input(CALIBRATED_INTENSITY)
            fired += int(brain.step()[brain.output_idx].sum())
        return fired / (400 * len(brain.output_idx))

    happy, sad = rate_with_mood(0.6), rate_with_mood(-1.0)
    print(f"  dopamine rate={happy:.4f}   pain rate={sad:.4f}   resting spikes ok")
    assert happy > sad, "dopamine should make the fly more responsive than pain"
    print("OK")


if __name__ == "__main__":
    _demo()
