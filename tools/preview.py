"""Capture this application's two pages for local visual verification."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tkinter as tk

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from speedtest_gui.app import SpeedTestApp, _enable_dpi_awareness
from speedtest_gui.config import SpeedTestConfig


def main():
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    _enable_dpi_awareness()
    root = tk.Tk()
    app = SpeedTestApp(root, config=SpeedTestConfig())
    get_parent = ctypes.windll.user32.GetParent
    get_parent.argtypes = [wintypes.HWND]
    get_parent.restype = wintypes.HWND

    def capture(name):
        root.update_idletasks()
        handle = get_parent(root.winfo_id())
        image = ImageGrab.grab(window=handle)
        image.save(output / name)

    def first_page():
        capture("speedtest_main_v1.png")
        app.notebook.select(app.settings_page)
        root.after(250, second_page)

    def second_page():
        capture("speedtest_settings_v1.png")
        (output / "文件改动记录.txt").write_text(
            "speedtest_main_v1.png、speedtest_settings_v1.png\n首版测速页和设置页的界面预览。\n",
            encoding="utf-8",
        )
        app.close()

    root.after(500, first_page)
    root.mainloop()
    print(output)


if __name__ == "__main__":
    main()
