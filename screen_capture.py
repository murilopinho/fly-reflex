"""Fast repeated screen capture via Spectacle's DBus daemon mode.

Spectacle normally spawns a whole Qt process per screenshot (~0.42s, 2.3 fps).
Running `spectacle -s` once and driving it over DBus reuses the same process,
reaching ~7 fps (see fly-brain/STATUS.md). Each capture writes a PNG under
~/Imagens/Capturas de tela/ and emits a ScreenshotTaken(filename) signal; we
load it with PIL and delete it immediately so the folder doesn't fill up.

Requires `spectacle -s &` running in the background before use.
"""
import os
import time

import dbus
import dbus.mainloop.glib
from gi.repository import GLib
from PIL import Image


class ScreenCapture:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self.iface = dbus.Interface(
            self.bus.get_object("org.kde.Spectacle", "/"), "org.kde.Spectacle"
        )
        self._loop = GLib.MainLoop()
        self._result = None
        self.bus.add_signal_receiver(
            self._on_taken, signal_name="ScreenshotTaken", dbus_interface="org.kde.Spectacle"
        )
        self.bus.add_signal_receiver(
            self._on_failed, signal_name="ScreenshotFailed", dbus_interface="org.kde.Spectacle"
        )

    def _on_taken(self, filename):
        self._result = str(filename)
        self._loop.quit()

    def _on_failed(self, message):
        self._result = RuntimeError(f"spectacle capture failed: {message}")
        self._loop.quit()

    def grab(self, box=None):
        """Take one screenshot. box=(x0,y0,x1,y1) crops before returning; None = full screen."""
        self._result = None
        self.iface.FullScreen(0)
        GLib.timeout_add_seconds(5, self._loop.quit)
        self._loop.run()

        if self._result is None:
            raise RuntimeError("spectacle capture timed out (is `spectacle -s` running?)")
        if isinstance(self._result, Exception):
            raise self._result

        path = self._result
        img = Image.open(path)
        img.load()
        os.remove(path)
        return img.crop(box) if box else img


def _demo():
    cap = ScreenCapture()
    t0 = time.time()
    n = 5
    for i in range(n):
        img = cap.grab()
        print(i, img.size, img.mode)
    dt = time.time() - t0
    print(f"{n} frames in {dt:.2f}s -> {n/dt:.1f} fps")


if __name__ == "__main__":
    _demo()
