import gc
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from speedtest_gui.app import SpeedTestApp, format_bytes, format_duration
from speedtest_gui.config import BUILTIN_COLLECTIONS, SpeedTestConfig
from tests.servers import HTTPFixture


class FormattingTests(unittest.TestCase):
    def test_decimal_bytes_and_elapsed(self):
        self.assertEqual(format_bytes(1_000_000), "1.00 MB")
        self.assertEqual(format_bytes(1_000_000_000), "1.00 GB")
        self.assertEqual(format_duration(65), "01:05")
        self.assertEqual(format_duration(3661), "01:01:01")


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "config.json"
        self.app = SpeedTestApp(self.root, config=SpeedTestConfig(), config_path=self.path)

    def tearDown(self):
        if not hasattr(self, "app"):
            return
        self.app.engine.stop()
        self.assertTrue(self.app.engine.wait(6))
        if self.app._after_id is not None:
            self.root.after_cancel(self.app._after_id)
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.directory.cleanup()
        # Tk interpreters must be finalized on the GUI thread. A later download
        # thread can otherwise trigger collection of a previous test's cycles.
        self.app = None
        self.root = None
        gc.collect()

    def pump_until(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return True
            time.sleep(0.02)
        return bool(condition())

    def test_pages_proxy_and_timer_controls(self):
        self.assertEqual([self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()], ["测速", "设置"])
        self.assertEqual(self.app.collection_var.get(), "Apple CDN")
        self.assertTrue(self.app.proxy_host_entry.instate(["disabled"]))
        self.assertTrue(self.app.duration_entry.instate(["disabled"]))
        self.app.proxy_mode_var.set("SOCKS5")
        self.app.timed_var.set(True)
        self.app._update_controls()
        self.assertTrue(self.app.proxy_host_entry.instate(["!disabled"]))
        self.assertTrue(self.app.duration_entry.instate(["!disabled"]))

    def test_collection_selection_and_restore(self):
        self.app.collection_var.set("Hugging Face Models")
        self.app.select_collection()
        self.assertEqual(
            self.app.urls_text.get("1.0", "end").strip().splitlines(),
            list(BUILTIN_COLLECTIONS[1].urls),
        )
        self.app.urls_text.delete("1.0", "end")
        self.app.urls_text.insert("1.0", "https://example.com/custom.bin")
        self.app.restore_collection_button.invoke()
        self.assertEqual(
            self.app.urls_text.get("1.0", "end").strip().splitlines(),
            list(BUILTIN_COLLECTIONS[1].urls),
        )
        self.app.collection_var.set("自定义")
        self.app.select_collection()
        self.assertTrue(self.app.restore_collection_button.instate(["disabled"]))

    def test_collection_is_saved_with_current_urls(self):
        self.app.collection_var.set("GitHub Releases")
        self.app.select_collection()
        self.assertTrue(self.app.save_settings())
        saved = SpeedTestConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        self.assertEqual(saved.collection_id, "github")
        self.assertEqual(saved.urls, BUILTIN_COLLECTIONS[2].urls)

    def test_invalid_settings_select_settings_page(self):
        self.app.connections_var.set("bad")
        with patch("speedtest_gui.app.messagebox.showerror") as showerror:
            self.assertFalse(self.app.save_settings())
            showerror.assert_called_once()
        self.assertEqual(self.app.notebook.select(), str(self.app.settings_page))
        self.assertFalse(self.path.exists())

    def test_running_locks_settings_and_stop_keeps_results(self):
        with HTTPFixture() as server:
            self.app.urls_text.delete("1.0", "end")
            self.app.urls_text.insert("1.0", server.url + "/stream")
            self.app.connections_var.set("2")
            self.app.start_button.invoke()
            self.assertTrue(self.app.start_button.instate(["disabled"]))
            self.assertEqual(self.app.notebook.tab(self.app.settings_page, "state"), "disabled")
            self.assertTrue(self.pump_until(lambda: self.app.engine.snapshot().total_bytes > 0))
            self.app.stop_button.invoke()
            self.assertTrue(self.pump_until(lambda: self.app.start_button.instate(["!disabled"])))
            self.assertGreater(self.app.engine.snapshot().total_bytes, 0)
            self.assertEqual(self.app.notebook.tab(self.app.settings_page, "state"), "normal")
            self.assertEqual(self.app.state_var.get(), "已停止")

    def test_close_waits_for_downloads(self):
        with HTTPFixture() as server:
            self.app.urls_text.delete("1.0", "end")
            self.app.urls_text.insert("1.0", server.url + "/slow")
            self.app.connections_var.set("1")
            self.app.start()
            self.assertTrue(self.pump_until(lambda: self.app.engine.snapshot().total_bytes > 0))
            self.app.close()
            self.assertTrue(self.pump_until(lambda: self.app._after_id is None))
            self.assertTrue(self.app.engine.wait(1))
