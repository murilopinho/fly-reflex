"""Continuous screen capture through the Wayland ScreenCast portal.

Spectacle works, but every grab writes a PNG to disk and raises a "screenshot
saved" popup — at 8 frames a second that buries the game under notifications.
The portal is the API actually meant for this: PipeWire hands us frames in
shared memory at display rate, with no disk writes and no popups.

The first run shows KDE's "share your screen" dialog. We ask the portal to
persist that consent and cache the token it returns, so later runs start
silently.

Falls back to nothing — the caller should catch the exception and use
screen_capture.ScreenCapture instead.
"""
import json
import os
import threading

import dbus
import dbus.mainloop.glib
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import GLib, Gst, GstApp  # noqa: E402

import numpy as np  # noqa: E402

TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".config", "fly-reflex-screencast.json")
PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"


class PipeWireCapture:
    """Latest-frame-wins screen capture. `.latest()` returns an RGB ndarray."""

    def __init__(self, timeout_s=60):
        Gst.init(None)
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        self._frame = None
        self._lock = threading.Lock()
        self._loop = GLib.MainLoop()
        self._session = None
        self._result = None

        self._negotiate(timeout_s)

    # -- portal handshake ------------------------------------------------

    def _request_path(self, token):
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def _call(self, method, *args, options=None):
        """Invoke a portal method and block until its Response signal lands."""
        token = f"flyreflex{os.getpid()}{abs(hash(method)) % 10000}"
        options = dict(options or {})
        options["handle_token"] = token
        path = self._request_path(token)

        self._result = None
        inner = GLib.MainLoop()

        def on_response(code, results):
            self._result = (int(code), results)
            inner.quit()

        match = self.bus.add_signal_receiver(
            on_response, signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request", path=path)

        iface = dbus.Interface(self.bus.get_object(PORTAL, PORTAL_PATH),
                               "org.freedesktop.portal.ScreenCast")
        getattr(iface, method)(*args, options)

        GLib.timeout_add_seconds(300, inner.quit)  # human has to click the KDE share dialog
        inner.run()
        match.remove()

        if self._result is None:
            raise RuntimeError(f"portal {method} timed out")
        code, results = self._result
        if code != 0:
            raise RuntimeError(f"portal {method} refused (code {code})")
        return results

    def _load_token(self):
        try:
            with open(TOKEN_FILE) as f:
                return json.load(f).get("restore_token")
        except (OSError, ValueError):
            return None

    def _save_token(self, token):
        if not token:
            return
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({"restore_token": str(token)}, f)

    def _negotiate(self, timeout_s):
        results = self._call("CreateSession", options={
            "session_handle_token": f"flyreflex{os.getpid()}"})
        self._session = results["session_handle"]

        select = {
            "types": dbus.UInt32(1),        # monitors
            "multiple": False,
            "cursor_mode": dbus.UInt32(1),  # hidden — the fly reads notes, not pointers
            "persist_mode": dbus.UInt32(2), # remember until explicitly revoked
        }
        saved = self._load_token()
        if saved:
            select["restore_token"] = saved
        self._call("SelectSources", self._session, options=select)

        results = self._call("Start", self._session, "")
        self._save_token(results.get("restore_token"))

        streams = results["streams"]
        if not streams:
            raise RuntimeError("portal returned no streams")
        node_id = int(streams[0][0])

        portal_obj = self.bus.get_object(PORTAL, PORTAL_PATH)
        iface = dbus.Interface(portal_obj, "org.freedesktop.portal.ScreenCast")
        fd_obj = iface.OpenPipeWireRemote(self._session, {})
        fd = fd_obj.take()

        self._start_pipeline(fd, node_id)

    # -- gstreamer -------------------------------------------------------

    def _start_pipeline(self, fd, node_id):
        desc = (
            f"pipewiresrc fd={fd} path={node_id} do-timestamp=true keepalive-time=1000 ! "
            "videoconvert ! video/x-raw,format=RGB ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        self.pipeline = Gst.parse_launch(desc)
        sink = self.pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        self.pipeline.set_state(Gst.State.PLAYING)

        self._thread = threading.Thread(target=self._loop.run, daemon=True)
        self._thread.start()

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                # rows are padded to a 4-byte stride boundary
                stride = info.size // h
                arr = np.frombuffer(info.data, dtype=np.uint8, count=h * stride)
                arr = arr.reshape(h, stride)[:, : w * 3].reshape(h, w, 3).copy()
                with self._lock:
                    self._frame = arr
            finally:
                buf.unmap(info)
        return Gst.FlowReturn.OK

    # -- public ----------------------------------------------------------

    def latest(self):
        with self._lock:
            return None if self._frame is None else self._frame

    def close(self):
        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        self._loop.quit()


def _demo():
    import time
    cap = PipeWireCapture()
    print("waiting for frames...")
    t0 = time.time()
    seen = 0
    last = None
    while time.time() - t0 < 4.0:
        f = cap.latest()
        if f is not None and (last is None or f is not last):
            seen += 1
            last = f
        time.sleep(0.01)
    assert last is not None, "no frames arrived from the portal"
    print(f"OK — frame {last.shape}, ~{seen} updates in 4s")
    cap.close()


if __name__ == "__main__":
    _demo()
