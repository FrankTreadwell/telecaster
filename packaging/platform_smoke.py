"""Exercise the real platform layer on a desktop session (used in CI on Windows and macOS).

Opens a target window, lays the overlay over it, flips click-through and
capture exclusion, and checks hotkeys construct. Exits non-zero on failure.
"""

import sys
import tempfile
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

import telecaster.app as tapp
from telecaster.overlay import Overlay
from telecaster.platforms import current as platform
from telecaster.strokes import StrokeStore

app = QApplication(sys.argv)


def pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def native_id(widget: QWidget) -> int:
    if sys.platform == "darwin":
        import objc
        return int(objc.objc_object(c_void_p=int(widget.winId())).window().windowNumber())
    return int(widget.winId())


target = QWidget()
target.setWindowTitle("Telecaster smoke target")
target.resize(640, 360)
target.move(120, 140)
target.show()
pump(1.0)

wid = native_id(target)
assert platform.window_exists(wid), "target window not found"
rect = platform.target_rect(wid)
print("target rect:", rect)
assert rect is not None and rect.w > 0 and rect.h > 0
print("listed windows:", len(platform.list_windows()))
platform.frontmost_window(exclude_pid=-1)
platform.is_in_front(wid, 0)

overlay = Overlay(StrokeStore())
overlay.place(rect)
pump(0.5)
for mode in ("draw", "calibrate", "passive"):
    overlay.set_mode(mode)
    pump(0.1)
overlay.set_exclude_from_capture(True)
print("overlay geometry:", overlay.geometry())

hotkeys = platform.GlobalHotkeys()
assert hotkeys.register(1, "D")
print("hotkey problem:", hotkeys.problem())
hotkeys.unregister_all()

settings_file = tempfile.NamedTemporaryFile(suffix=".ini", delete=False).name
tapp.QSettings = lambda *_: QSettings(settings_file, QSettings.IniFormat)
window = tapp.MainWindow()
window.show()
pump(0.5)
window.close()
print("PLATFORM SMOKE OK")
