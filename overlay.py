"""On-screen overlay: the real fly brain, rendered live, on top of the game.

Left to itself this draws three things, all fed by the running simulation:

  * the actual FlyWire brain — 23,210 real soma coordinates as a dim point
    cloud (that IS the shape of a fruit fly's brain), with the 668 neurons of
    our circuit overlaid and flashing white as they spike
  * the fly itself, startling when the giant fiber (DNp01) fires
  * a readout panel: per-lane descending firing rate in Hz, which key the
    circuit is pressing, and the running spike count

Rendering is numpy splatting into an RGB buffer wrapped as a QImage — 24k
points a frame costs nothing and additive splatting gives the glow for free.

Run it through run.py; this module only draws what it is handed.
"""
import json
import os

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN_POINTS = os.path.join(HERE, "desktop-fly", "data", "brain_points.json")
FLY_SPRITE = os.path.join(HERE, "desktop-fly", "assets", "fly.png")

W, H = 540, 660
BRAIN_H = 252
LANE_NAMES = ["left", "down", "up", "right"]

# Fonts confirmed present on this machine; Qt silently falls back otherwise,
# which is what broke the number alignment the first time around.
FONT_MONO = "Hack"
FONT_SANS = "Noto Sans"
FONT_DISPLAY = "Cantarell"

INK = QColor(232, 237, 246)
MUTED = QColor(136, 145, 163)
FAINT = QColor(84, 94, 112)
PANEL = QColor(14, 17, 24, 246)
LINE = QColor(255, 255, 255, 26)
HOT = QColor(255, 77, 109)
COOL = QColor(46, 230, 184)
AMBER = QColor(255, 182, 72)


def paw_print(p, cx, cy, size):
    """A little paw stamped over the arrow the instant she presses — same
    spot the white ring used to mark, just cuter and easier to read at a
    glance while debugging which lane actually fired."""
    pad = size * 0.42
    p.setPen(Qt.NoPen)
    p.setBrush(INK)
    p.drawEllipse(QPointF(cx, cy + pad * 0.15), pad, pad * 0.85)
    r = pad * 0.36
    for dx, dy in ((-pad * 0.75, -pad * 0.95), (-pad * 0.25, -pad * 1.25),
                   (pad * 0.25, -pad * 1.25), (pad * 0.75, -pad * 0.95)):
        p.drawEllipse(QPointF(cx + dx, cy + dy), r, r)


def arrow_polygon(cx, cy, size, direction):
    """An FNF-shaped arrowhead, pointing the way that lane's key does."""
    h = size / 2.0
    points = {
        "left":  [(-h, 0), (h, -h), (h, h)],
        "right": [(h, 0), (-h, -h), (-h, h)],
        "up":    [(0, -h), (-h, h), (h, h)],
        "down":  [(0, h), (-h, -h), (h, -h)],
    }[direction]
    return QPolygonF([QPointF(cx + dx, cy + dy) for dx, dy in points])


class BrainRenderer:
    """Projects the real 3D brain to a 2D glow buffer."""

    def __init__(self, width, height):
        self.w, self.h = width, height
        d = json.load(open(BRAIN_POINTS))
        pts = np.asarray(d["points"], dtype=np.float32)
        self.bg_xyz = pts[:, :3]
        self.bg_class = pts[:, 3].astype(np.int32)
        self.classes = d["classes"]

        # dim steel blue for bulk tissue; optic lobes slightly cooler
        self.bg_color = np.full((len(self.bg_xyz), 3), (26, 48, 78), dtype=np.float32)
        self.bg_color[self.bg_class == 0] = (22, 54, 74)     # optic
        self.bg_color[self.bg_class == 5] = (70, 40, 60)     # descending — hint of magenta

        # fill the panel width with the brain's widest axis, leaving a small margin
        x_span = float(np.abs(self.bg_xyz[:, 0]).max())
        self.scale = self.w / (2.2 * x_span)
        self.angle = 0.0
        self._buf = None

    def _project(self, xyz, angle):
        c, s = np.cos(angle), np.sin(angle)
        x = xyz[:, 0] * c + xyz[:, 2] * s
        z = -xyz[:, 0] * s + xyz[:, 2] * c
        y = xyz[:, 1]
        px = (x * self.scale + self.w * 0.5).astype(np.int32)
        py = (-y * self.scale + self.h * 0.5).astype(np.int32)
        return px, py, z

    def render(self, circuit_xyz, glow, roles):
        """glow: per-circuit-neuron 0..1 activity. roles: int array (0 input, 1 output, 2 gf, 3 other)."""
        buf = np.zeros((self.h, self.w, 3), dtype=np.float32)

        px, py, z = self._project(self.bg_xyz, self.angle)
        ok = (px >= 0) & (px < self.w) & (py >= 0) & (py < self.h)
        zmin, zmax = float(z.min()), float(z.max())
        depth = ((z - zmin) / max(1e-6, zmax - zmin) * 0.75 + 0.25)[ok, None]
        np.add.at(buf, (py[ok], px[ok]), self.bg_color[ok] * depth)

        cx, cy, cz = self._project(circuit_xyz, self.angle)
        role_color = np.array([
            (255, 182, 72),    # 0 looming input  — amber
            (255, 77, 109),    # 1 descending out — hot pink
            (255, 255, 255),   # 2 giant fiber    — white
            (60, 110, 150),    # 3 other          — steel
        ], dtype=np.float32)
        base = role_color[roles]
        lit = base * (0.22 + 2.4 * glow[:, None])

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                sx, sy = cx + dx, cy + dy
                ok = (sx >= 0) & (sx < self.w) & (sy >= 0) & (sy < self.h)
                weight = 1.0 if (dx == 0 and dy == 0) else 0.45
                np.add.at(buf, (sy[ok], sx[ok]), lit[ok] * weight)

        np.clip(buf, 0, 255, out=buf)
        self._buf = np.ascontiguousarray(buf.astype(np.uint8))
        return QImage(self._buf.data, self.w, self.h, self.w * 3, QImage.Format_RGB888)


class Overlay(QWidget):
    stim_toggle = Signal()

    def __init__(self, brain_meta, demo=False):
        super().__init__()
        self.setWindowTitle("Fly Reflex")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(W, H)
        self.demo = demo

        self.renderer = BrainRenderer(W - 32, BRAIN_H)
        self.circuit_xyz = brain_meta["pos"]
        self.roles = brain_meta["roles"]
        self.glow = np.zeros(len(self.circuit_xyz), dtype=np.float32)

        self.fly = self._load_fly()
        self.sway = 0.0
        self.state = {
            "lane_hz": {n: 0.0 for n in LANE_NAMES},
            "pressed": None,
            "gf_hz": 0.0,
            "total_spikes": 0,
            "mood": 0.0,
            "status": "starting",
            "fps": 0.0,
            "score": 0,
            "combo": 0,
            "judgment": "",
            "accuracy": 0.0,
            "armed": False,
        }
        self.startle = 0.0
        self._drag = None

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

        # KWin doesn't always honor WindowStaysOnTopHint for an XWayland
        # client against native Wayland windows (Firefox) once they're
        # raised/focused — re-assert on top periodically instead of once.
        self.raise_timer = QTimer(self)
        self.raise_timer.timeout.connect(self.raise_)
        self.raise_timer.start(1000)

    def _load_fly(self):
        """The upstream sprite ships on a flat light-grey card; key it out so
        the fly sits on the panel instead of inside a white box."""
        from PIL import Image

        im = np.array(Image.open(FLY_SPRITE).convert("RGBA"))
        rgb = im[:, :, :3].astype(np.int16)
        flat = (rgb.max(axis=2) - rgb.min(axis=2)) < 20
        im[flat & (rgb.min(axis=2) > 200), 3] = 0
        im = np.ascontiguousarray(im)
        self._fly_buf = im
        qimg = QImage(im.data, im.shape[1], im.shape[0], im.shape[1] * 4, QImage.Format_RGBA8888)
        return QPixmap.fromImage(qimg).scaledToWidth(118, Qt.SmoothTransformation)

    # --- data in -------------------------------------------------------

    def push(self, spikes, info):
        """Called from the simulation thread's Qt signal."""
        self.glow = np.maximum(self.glow, spikes.astype(np.float32))
        self.state.update(info)
        if info.get("gf_fired"):
            self.startle = 1.0

    def _tick(self):
        self.glow *= 0.86
        self.startle *= 0.90
        # Sway around the frontal view instead of spinning: a full turntable
        # spends half its time edge-on, where the brain reads as a blob and
        # the optic lobes stop being recognisable.
        self.sway += 0.012
        self.renderer.angle = 0.42 * np.sin(self.sway)
        self.update()

    # --- window dragging ----------------------------------------------

    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() == Qt.Key_S and self.demo:
            self.stim_toggle.emit()

    # --- painting ------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        p.setPen(Qt.NoPen)
        p.setBrush(PANEL)
        p.drawRoundedRect(self.rect(), 14, 14)
        p.setPen(QPen(LINE, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)

        y = self._paint_header(p, 18)
        y = self._paint_brain(p, y)
        y = self._paint_fly_row(p, y)
        y = self._paint_lanes(p, y)
        self._paint_footer(p, y)
        p.end()

    def _font(self, size, weight=QFont.Normal, mono=False, display=False):
        family = FONT_MONO if mono else (FONT_DISPLAY if display else FONT_SANS)
        f = QFont(family, size, weight)
        f.setStyleHint(QFont.Monospace if mono else QFont.SansSerif)
        return f

    def _label(self, p, x, y, text, color=FAINT, size=7, spacing=1.6):
        f = self._font(size, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        f.setCapitalization(QFont.AllUppercase)
        p.setFont(f)
        p.setPen(color)
        p.drawText(x, y, text)

    def _paint_header(self, p, y):
        p.setFont(self._font(17, QFont.Bold, display=True))
        p.setPen(INK)
        p.drawText(18, y + 14, "FLY")
        wfly = p.fontMetrics().horizontalAdvance("FLY")
        p.setPen(HOT)
        p.drawText(18 + wfly, y + 14, "REFLEX")

        live = self.state.get("status", "")
        dot = COOL if live == "live" else AMBER
        p.setBrush(dot)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(W - 92, y + 9), 3.5, 3.5)
        self._label(p, W - 80, y + 13, live or "idle", dot, 7)

        self._label(p, 18, y + 30, "FlyWire v783 · 668-neuron circuit · live", MUTED, 7, 0.8)
        return y + 44

    def _paint_brain(self, p, y):
        img = self.renderer.render(self.circuit_xyz, self.glow, self.roles)
        p.drawImage(16, y, img)

        self._label(p, 26, y + 16, "connectome · 23,210 real soma coordinates", FAINT, 7, 1.0)
        legend = [("looming in", AMBER), ("descending out", HOT), ("giant fiber", INK)]
        lx = 26
        for name, color in legend:
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(lx + 3, y + BRAIN_H - 14), 3, 3)
            self._label(p, lx + 12, y + BRAIN_H - 11, name, MUTED, 7, 0.8)
            lx += 12 + p.fontMetrics().horizontalAdvance(name.upper()) + 22
        return y + BRAIN_H + 14

    def _paint_fly_row(self, p, y):
        jitter = self.startle * 7
        fx = 24 + (np.random.uniform(-jitter, jitter) if jitter > 0.4 else 0)
        fy = y + (np.random.uniform(-jitter, jitter) if jitter > 0.4 else 0)
        p.drawPixmap(int(fx), int(fy), self.fly)

        bx = 170
        self._label(p, bx, y + 22, "giant fiber · DNp01", FAINT)
        hz = self.state.get("gf_hz", 0.0)
        text = f"{hz:.1f}"
        p.setFont(self._font(24, QFont.Bold, mono=True))
        p.setPen(INK if self.startle < 0.15 else HOT)
        p.drawText(bx, y + 54, text)
        num_w = p.fontMetrics().horizontalAdvance(text)
        p.setFont(self._font(9))
        p.setPen(MUTED)
        p.drawText(bx + num_w + 6, y + 54, "Hz")

        self._label(p, bx, y + 80, "reaction", FAINT)
        p.setFont(self._font(11, QFont.DemiBold))
        p.setPen(HOT if self.startle > 0.25 else MUTED)
        p.drawText(bx, y + 98, "PRESS" if self.startle > 0.25 else "waiting")

        mood = float(self.state.get("mood", 0.0))
        self._label(p, bx + 150, y + 22, "mood", FAINT)
        tx, tw = bx + 150, 110
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(tx, y + 32, tw, 6, 3, 3)
        p.setBrush(COOL if mood >= 0 else HOT)
        cx = tx + tw * (mood + 1) / 2
        p.drawEllipse(QPointF(cx, y + 35), 4, 4)
        p.setFont(self._font(8, mono=True))
        p.setPen(MUTED)
        p.drawText(tx, y + 54, "pain")
        p.drawText(tx + tw - 42, y + 54, "dopamine")

        judgment = self.state.get("judgment", "")
        self._label(p, tx, y + 80, "last note", FAINT)
        p.setFont(self._font(11, QFont.DemiBold))
        p.setPen(HOT if judgment in ("Miss", "Ghost") else (COOL if judgment else MUTED))
        p.drawText(tx, y + 98, judgment or "—")

        sx = 442
        self._label(p, sx, y + 22, "score", FAINT)
        p.setFont(self._font(14, QFont.Bold, mono=True))
        p.setPen(INK)
        p.drawText(sx, y + 46, f"{self.state.get('score', 0):,}")

        self._label(p, sx, y + 80, "combo", FAINT)
        combo = self.state.get("combo", 0)
        p.setFont(self._font(11, QFont.DemiBold, mono=True))
        p.setPen(COOL if combo > 0 else MUTED)
        p.drawText(sx, y + 98, f"x{combo}")
        return y + 128

    def _paint_lanes(self, p, y):
        self._label(p, 18, y, "descending output per lane", FAINT)
        y += 12
        pressed = self.state.get("pressed")
        due_all = self.state.get("note_here", {})
        for name in LANE_NAMES:
            hz = self.state["lane_hz"].get(name, 0.0)
            is_press = name == pressed
            p.setFont(self._font(9, QFont.DemiBold))
            p.setPen(COOL if is_press else MUTED)
            p.drawText(20, y + 15, name.upper())

            # The arrow fills in when the judge says a note is on the line right
            # now, and gets a ring the instant she presses. Lit arrow + ring at
            # the same time is a hit you can see; ring on a dark arrow is her
            # swinging at nothing.
            due = min(1.0, max(0.0, due_all.get(name, 0.0)))
            poly = arrow_polygon(67, y + 10, 17, name)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COOL.red(), COOL.green(), COOL.blue(), int(30 + 225 * due)))
            p.drawPolygon(poly)
            if is_press:
                paw_print(p, 67, y + 10, 15)

            bx, bw = 86, 288
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(bx, y + 5, bw, 11, 5, 5)
            frac = min(1.0, hz / 40.0)
            if frac > 0.01:
                p.setBrush(COOL if is_press else HOT)
                p.drawRoundedRect(bx, y + 5, int(bw * frac), 11, 5, 5)

            p.setFont(self._font(10, QFont.DemiBold, mono=True))
            p.setPen(INK if is_press else MUTED)
            reading = f"{hz:5.1f} Hz"
            p.drawText(bx + bw + 10, y + 15, reading)
            reading_w = p.fontMetrics().horizontalAdvance(reading)  # measure in the mono font

            if is_press:
                p.setFont(self._font(8, QFont.Bold))
                p.setPen(COOL)
                p.drawText(bx + bw + 18 + reading_w, y + 15, "KEY")
            y += 24
        return y + 8

    def _paint_footer(self, p, y):
        p.setPen(QPen(LINE, 1))
        p.drawLine(18, y, W - 18, y)
        self._label(p, 18, y + 18, "total spikes", FAINT)
        p.setFont(self._font(13, QFont.DemiBold, mono=True))
        p.setPen(INK)
        p.drawText(18, y + 40, f"{self.state.get('total_spikes', 0):,}")

        self._label(p, 210, y + 18, "sim rate", FAINT)
        p.setFont(self._font(13, QFont.DemiBold, mono=True))
        p.setPen(INK)
        p.drawText(210, y + 40, f"{self.state.get('fps', 0.0):.0f} Hz")

        self._label(p, 330, y + 18, "accuracy", FAINT)
        p.setFont(self._font(13, QFont.DemiBold, mono=True))
        p.setPen(INK)
        p.drawText(330, y + 40, f"{self.state.get('accuracy', 0.0):.0f}%")

        armed = self.state.get("armed", False)
        self._label(p, 430, y + 18, "keyboard", FAINT)
        p.setFont(self._font(13, QFont.DemiBold, mono=True))
        if self.demo:
            stim_on = self.state.get("stim_on", True)
            p.setPen(AMBER if stim_on else COOL)
            p.drawText(430, y + 40, "DEMO" if stim_on else "PAUSED")
        else:
            p.setPen(HOT if armed else COOL)
            p.drawText(430, y + 40, "FLY" if armed else "YOURS")

        hint = "drag to move · f9 arms the fly · esc to quit"
        if self.demo:
            hint = "drag to move · s pauses the notes · esc to quit"
        self._label(p, 18, y + 60, hint, FAINT, 7, 0.8)
