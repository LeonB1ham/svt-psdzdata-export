"""Capture live GUI screenshots (English UI) into demo/screenshots. Windows only.

py -3 demo/capture_screens.py
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as gui  # noqa: E402
from engine import nearest_version  # noqa: E402

SHOTS = Path(__file__).resolve().parent / "screenshots"
user32 = ctypes.windll.user32
GA_ROOT = 2
WIDTH, HEIGHT = 1260, 920
DWMWA_EXTENDED_FRAME_BOUNDS = 9


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()


def _bbox(widget) -> tuple[int, int, int, int]:
    widget.update_idletasks()
    widget.update()
    hwnd = user32.GetAncestor(widget.winfo_id(), GA_ROOT)
    rect = wintypes.RECT()
    try:
        # Visible frame without the invisible resize border/shadow
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        )
    except Exception:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def grab(widget, name: str) -> None:
    widget.lift()
    widget.attributes("-topmost", True)
    widget.update()
    image = ImageGrab.grab(bbox=_bbox(widget), all_screens=True)
    image.save(SHOTS / name)
    print(f"saved {name} ({image.size[0]}x{image.size[1]})")
    widget.attributes("-topmost", False)


class Script:
    def __init__(self) -> None:
        self.app = gui.App(lang="en", use_config=False)
        app = self.app
        x = max((app.winfo_screenwidth() - WIDTH) // 2, 0)
        y = max((app.winfo_screenheight() - HEIGHT) // 2 - 20, 0)
        app.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        app._on_search_done = lambda: app.after(500, self.next)
        self.steps = [
            self.single,
            self.single_shot,
            self.diff,
            self.diff_shot,
            self.missing_shot,
            self.tal,
            self.tal_shot,
            self.batch,
            self.batch_shot,
        ]

    def run(self) -> None:
        self.app.after(600, self.next)
        self.app.mainloop()

    def next(self) -> None:
        if self.steps:
            step = self.steps.pop(0)
            step()
        else:
            self.app.destroy()

    def _then(self, delay: int = 400) -> None:
        self.app.after(delay, self.next)

    # Single SVT: found / other version / incomplete pair / missing
    def single(self) -> None:
        self.app.load_demo_paths("single")
        self.app.start_search()

    def single_shot(self) -> None:
        grab(self.app, "01-single.png")
        self._then()

    # Ist -> Soll: only what changes, one part replaced by the nearest version
    def diff(self) -> None:
        self.app.mode_var.set("diff")
        self.app._on_mode()
        self.app.start_search()

    def diff_shot(self) -> None:
        app = self.app
        for iid, match in app.part_by_iid.items():
            if match.part.process_class and not match.found and match.other_versions:
                app._set_override(iid, nearest_version(match))
        app.hide_same_var.set(False)
        app._render_tree()
        app.update()
        grab(app, "02-ist-soll.png")
        self._then()

    def missing_shot(self) -> None:
        app = self.app
        for iid, match in app.part_by_iid.items():
            if match.override_version:
                app._set_override(iid, "")
        dialog = app.show_missing_dialog()
        dialog.car_var.set("F30 demo")
        dialog.istep_var.set("F020-24-07-550")
        x = app.winfo_rootx() + 260
        y = app.winfo_rooty() + 120
        dialog.geometry(f"720x560+{x}+{y}")
        dialog.update()

        def shot() -> None:
            grab(dialog, "03-missing-list.png")
            dialog.destroy()
            self._then()

        app.after(500, shot)

    def tal(self) -> None:
        self.app.mode_var.set("tal")
        self.app._on_mode()
        self.app.start_search()

    def tal_shot(self) -> None:
        grab(self.app, "04-tal.png")
        self._then()

    def batch(self) -> None:
        app = self.app
        app.mode_var.set("batch")
        app._on_mode()
        demo = ROOT / "demo"
        app.batch_files = [str(demo / "sample-svt.xml"), str(demo / "sample-svt-ist.xml"), str(demo / "sample-tal.xml")]
        app.zip_var.set(True)
        app._render_batch()
        app.start_search()

    def batch_shot(self) -> None:
        grab(self.app, "05-batch.png")
        self._then()


def main() -> None:
    _enable_dpi()
    SHOTS.mkdir(parents=True, exist_ok=True)
    Script().run()


if __name__ == "__main__":
    main()
