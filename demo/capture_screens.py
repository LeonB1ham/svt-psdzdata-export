"""Capture live GUI screenshots into demo/screenshots."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import App  # noqa: E402
from i18n import LANG_NAMES  # noqa: E402

SHOTS = Path(__file__).resolve().parent / "screenshots"
user32 = ctypes.windll.user32
GA_ROOT = 2


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()


def _window_bbox(app: App) -> tuple[int, int, int, int]:
    app.update_idletasks()
    app.update()
    hwnd = user32.GetAncestor(app.winfo_id(), GA_ROOT)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def grab(app: App, name: str) -> Path:
    app.lift()
    app.attributes("-topmost", True)
    app.update()
    path = SHOTS / name
    image = ImageGrab.grab(bbox=_window_bbox(app), all_screens=True)
    image.save(path)
    print(f"saved {path} ({image.size[0]}x{image.size[1]})")
    return path


def main() -> None:
    _enable_dpi()
    SHOTS.mkdir(parents=True, exist_ok=True)

    app = App()
    app.remember_paths = False
    app._lang = "en"
    app.lang_var.set(LANG_NAMES["en"])
    app._apply_language()
    app.load_demo_paths()
    app.update_idletasks()
    width, height = 1180, 760
    x = max((app.winfo_screenwidth() - width) // 2, 40)
    y = max((app.winfo_screenheight() - height) // 2, 40)
    app.geometry(f"{width}x{height}+{x}+{y}")

    def after_search() -> None:
        for iid in app.tree.get_children(""):
            app.tree.item(iid, open=True)
        app.update()
        app.after(400, take_results)

    def take_results() -> None:
        grab(app, "02-results.png")
        # Leave only HKFM2 checked to show mixed selection.
        for iid, match in app.row_matches.items():
            app.checked[iid] = match.ecu.base_variant == "HKFM2"
        app._refresh_all_marks()
        app.update()
        app.after(300, take_selection)

    def take_selection() -> None:
        grab(app, "03-selection.png")
        app.attributes("-topmost", False)
        app.destroy()

    app._on_search_done = after_search
    app.after(500, lambda: (grab(app, "01-start.png"), app.start_search()))
    app.mainloop()


if __name__ == "__main__":
    main()
