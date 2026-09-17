"""Where the FNF note lanes are on screen, found automatically when possible.

The strumline is a row of 8 bright arrow icons: the opponent's four on the
left, the player's four on the right. `autocalibrate()` finds that row in a
screenshot instead of trusting fixed pixels, because the game's embed shifts
whenever the page changes state (measured ~20px vertically between the song
select menu and a song actually playing, with x staying put).

LANES below is the last known-good measurement — the fallback for when
detection fails, e.g. the strumline is off screen or a cutscene covers it.
Calibrated against Firefox kiosk
(`firefox --kiosk https://www.kbhgames.com/game/friday-night-funkin/`) at
1920x1080.
"""
import numpy as np

CANVAS = (302, 204, 1263, 744)  # (x0, y0, x1, y1) of the game video area

# player's 4 lanes: left, down, up, right — each (x0, y0, x1, y1)
LANES = {
    "left":  (824, 204, 893, 322),
    "down":  (911, 204, 981, 322),
    "up":    (999, 204, 1061, 322),
    "right": (1081, 204, 1148, 322),
}

# ydotool key codes (Linux input-event-codes.h) for injecting FNF key presses
KEYCODES = {
    "left": 105,
    "down": 108,
    "up": 103,
    "right": 106,
}

LANE_ORDER = ["left", "down", "up", "right"]
_ARROW_MIN_W, _ARROW_MAX_W = 40, 100
_BRIGHT = 120


def _bright_runs(row):
    """Contiguous spans of bright pixels wide enough to be an arrow icon."""
    lit = row > _BRIGHT
    edges = np.diff(lit.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    ends = list(np.flatnonzero(edges == -1))
    if lit[0]:
        starts.insert(0, 0)
    if lit[-1]:
        ends.append(len(lit) - 1)
    return [(s, e) for s, e in zip(starts, ends) if _ARROW_MIN_W <= e - s <= _ARROW_MAX_W]


def autocalibrate(img, search_rows=range(150, 460, 4)):
    """Locate the player's four lanes in a full screenshot.

    Returns (lanes_dict, info_string), or (None, reason) if the strumline
    isn't findable — the caller should then fall back to LANES.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    if gray.shape[0] < 500:
        return None, "screenshot too small"

    best_row, best_runs = None, []
    for y in search_rows:
        runs = _bright_runs(gray[y])
        if len(runs) > len(best_runs):
            best_row, best_runs = y, runs

    if best_row is None or len(best_runs) < 3:
        return None, f"found only {len(best_runs)} arrow-shaped runs"

    # The player's lanes are the rightmost four. Characters standing in front
    # of the strumline routinely swallow one arrow, so rebuild the set from the
    # spacing between whichever ones did survive.
    runs = sorted(best_runs)[-4:]
    widths = [e - s for s, e in runs]
    width = int(np.median(widths))
    lefts = [s for s, _ in runs]
    if len(runs) >= 2:
        pitch = int(np.median(np.diff(lefts))) if len(runs) > 2 else lefts[1] - lefts[0]
    else:
        return None, "not enough arrows to infer lane spacing"
    if not (60 <= pitch <= 140):
        return None, f"implausible lane spacing ({pitch}px)"

    # anchor on the leftmost surviving arrow and step by the measured pitch
    x0 = lefts[0] - pitch * (4 - len(runs)) if len(runs) < 4 else lefts[0]

    column = gray[:, min(gray.shape[1] - 1, x0 + width // 2)]
    band = np.flatnonzero(column[best_row - 60:best_row + 60] > _BRIGHT)
    y_bottom = (best_row - 60 + band.max() + 12) if band.size else best_row + 40
    y_top = max(0, best_row - 130)

    lanes = {}
    for i, name in enumerate(LANE_ORDER):
        left = int(x0 + i * pitch)
        lanes[name] = (left, int(y_top), int(left + width), int(y_bottom))
    return lanes, f"strumline at y={best_row}, {len(runs)} arrows seen, pitch {pitch}px"


def _demo():
    """Self-check against a synthetic strumline, so this runs without the game."""
    from PIL import Image

    img = Image.new("L", (1920, 1080), 20)
    px = img.load()
    truth_x0, pitch, width = 824, 88, 68
    for group in (330, truth_x0):                    # opponent + player rows
        for i in range(4):
            left = group + i * pitch
            for x in range(left, left + width):
                for y in range(250, 300):
                    px[x, y] = 220

    lanes, info = autocalibrate(img)
    assert lanes is not None, f"detection failed: {info}"
    got = lanes["left"][0]
    assert abs(got - truth_x0) <= pitch // 2, f"left lane at {got}, expected ~{truth_x0}"
    assert abs((lanes["down"][0] - lanes["left"][0]) - pitch) <= 4, "wrong lane pitch"
    print(f"autocalibrate OK — {info}")
    print(f"  lanes: { {k: v for k, v in lanes.items()} }")


if __name__ == "__main__":
    _demo()
