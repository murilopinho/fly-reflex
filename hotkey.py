"""Global keyboard watch, read straight off /dev/input.

This is the only way to catch a hotkey while the game has focus: Qt shortcuts
only fire when the overlay itself is focused, and Wayland has no global-hotkey
API for an unprivileged client. Reading the evdev stream needs nothing but
membership in the `input` group, which this machine already has (it is what the
ydotool socket override set up).

Two jobs:
  * F9 arms and disarms the fly's key injection
  * any other real keypress disarms it on the spot, so the moment you start
    typing the fly stops fighting you for the keyboard

Only hardware keyboards are watched. ydotoold's own virtual device shows up in
/dev/input exactly like a real one, and watching it would make every key the
fly injects look like a human at the keyboard — it would stand itself down on
its first note. It is excluded by name.
"""
import os
import select
import struct
import threading

EVENT_FORMAT = "llHHi"      # struct input_event: timeval, type, code, value
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 0x01
KEY_DOWN = 1
TOGGLE_KEY = 67             # F9 — FNF binds none of the function keys
VIRTUAL_HINTS = ("ydotool", "uinput", "virtual")


def keyboard_devices():
    """Readable hardware keyboards from /proc/bus/input/devices."""
    devices, name = [], ""
    try:
        lines = open("/proc/bus/input/devices").read().splitlines()
    except OSError:
        return devices
    for line in lines:
        if line.startswith("N: Name="):
            name = line.split("=", 1)[1].strip().strip('"').lower()
        elif line.startswith("H: Handlers=") and "kbd" in line:
            if any(hint in name for hint in VIRTUAL_HINTS):
                continue
            for token in line.split("=", 1)[1].split():
                if token.startswith("event"):
                    path = f"/dev/input/{token}"
                    if os.access(path, os.R_OK):
                        devices.append(path)
    return devices


def dispatch(data, on_toggle, on_other_key):
    """Parse a raw evdev read and fire the callbacks. Returns keys seen."""
    seen = 0
    for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
        _, _, etype, code, value = struct.unpack(EVENT_FORMAT, data[off:off + EVENT_SIZE])
        if etype != EV_KEY or value != KEY_DOWN:
            continue
        seen += 1
        if code == TOGGLE_KEY:
            on_toggle()
        else:
            on_other_key()
    return seen


class KeyboardWatch(threading.Thread):
    """Watches the real keyboards and reports F9 / human activity."""

    def __init__(self, on_toggle, on_other_key):
        super().__init__(daemon=True)
        self.on_toggle = on_toggle
        self.on_other_key = on_other_key
        self.running = True
        self.error = None
        self.devices = keyboard_devices()
        if not self.devices:
            self.error = ("no readable keyboard in /dev/input — the F9 toggle is off. "
                          "Add yourself to the `input` group to get it back.")

    def run(self):
        if self.error:
            return
        fds = {}
        for path in self.devices:
            try:
                fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
            except OSError:
                pass
        if not fds:
            self.error = "could not open any keyboard device"
            return
        try:
            while self.running:
                ready, _, _ = select.select(list(fds), [], [], 0.2)
                for fd in ready:
                    try:
                        data = os.read(fd, EVENT_SIZE * 64)
                    except (BlockingIOError, OSError):
                        continue
                    dispatch(data, self.on_toggle, self.on_other_key)
        finally:
            for fd in fds:
                os.close(fd)


def _event(code, value, etype=EV_KEY):
    return struct.pack(EVENT_FORMAT, 0, 0, etype, code, value)


def _demo():
    """Self-check: the parser must tell F9 apart from ordinary typing, and must
    ignore key-up and non-key events — otherwise every keystroke would count
    twice and the fly would flip its own arm state."""
    hits = {"toggle": 0, "other": 0}
    data = (
        _event(TOGGLE_KEY, KEY_DOWN)        # F9 pressed
        + _event(TOGGLE_KEY, 0)             # F9 released — must not count
        + _event(30, KEY_DOWN)              # 'a'
        + _event(30, 2)                     # 'a' autorepeat — must not count
        + _event(0, KEY_DOWN, etype=0x00)   # EV_SYN — must not count
    )
    seen = dispatch(data,
                    lambda: hits.__setitem__("toggle", hits["toggle"] + 1),
                    lambda: hits.__setitem__("other", hits["other"] + 1))
    assert seen == 2, f"expected 2 key-down events, got {seen}"
    assert hits == {"toggle": 1, "other": 1}, hits
    assert EVENT_SIZE == 24, f"input_event should be 24 bytes here, got {EVENT_SIZE}"

    found = keyboard_devices()
    print(f"parser OK — hardware keyboards: {found or 'NONE (toggle would be unavailable)'}")
    for line in open("/proc/bus/input/devices").read().split("\n\n"):
        if "ydotool" in line.lower():
            handler = [l for l in line.splitlines() if l.startswith("H:")]
            assert not any(h.split("=")[1].split()[0] in str(found) for h in handler if "=" in h), \
                "ydotool's virtual device must never be watched"
            print("ydotool virtual device correctly excluded")
    print("OK")


if __name__ == "__main__":
    _demo()
