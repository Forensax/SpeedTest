import gc
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from speedtest_gui.app import NOTEBOOK_TAB_PADDING, NOTEBOOK_TAB_WIDTH, SpeedTestApp, format_bytes, format_duration
from speedtest_gui.config import BUILTIN_COLLECTIONS, COLLECTIONS_BY_LABEL, SpeedTestConfig
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

    def reopen(self):
        if self.app._after_id is not None:
            self.root.after_cancel(self.app._after_id)
        self.root.destroy()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = SpeedTestApp(self.root, config_path=self.path)

    def test_pages_proxy_and_timer_controls(self):
        self.assertEqual([self.app.notebook.tab(tab, "text") for tab in self.app.notebook.tabs()], ["测速", "设置"])
        self.assertEqual(int(self.root.tk.call("ttk::style", "lookup", "TNotebook.Tab", "-width")), NOTEBOOK_TAB_WIDTH)
        self.assertEqual(
            ttk.Style(self.root).map("TNotebook.Tab", "padding"),
            [("selected", "18 8"), ("!selected", "18 8")],
        )
        self.assertEqual(NOTEBOOK_TAB_PADDING, (18, 8))
        self.assertEqual(self.app.collection_var.get(), "Apple CDN")
        self.assertEqual(list(self.app.collection_buttons), [item.id for item in BUILTIN_COLLECTIONS] + ["custom"])
        self.assertEqual(self.app.collection_buttons["apple"].cget("style"), "Primary.TButton")
        self.assertEqual(self.app.collection_buttons["github"].cget("style"), "App.TButton")
        self.assertIs(self.app.restore_collection_button.master, self.app.urls_header)
        self.assertTrue(self.app.proxy_host_entry.instate(["disabled"]))
        self.assertTrue(self.app.duration_entry.instate(["disabled"]))
        self.app.proxy_mode_var.set("SOCKS5")
        self.app.timed_var.set(True)
        self.app._update_controls()
        self.assertTrue(self.app.proxy_host_entry.instate(["!disabled"]))
        self.assertTrue(self.app.duration_entry.instate(["!disabled"]))

    def test_collection_buttons_select_collection(self):
        self.app.collection_buttons["huggingface"].invoke()
        self.assertEqual(self.app.collection_var.get(), "Hugging Face Models")
        self.assertEqual(
            self.app.urls_text.get("1.0", "end").strip().splitlines(),
            list(BUILTIN_COLLECTIONS[1].urls),
        )
        self.assertEqual(self.app.collection_buttons["huggingface"].cget("style"), "Primary.TButton")
        self.app.collection_buttons["custom"].invoke()
        self.assertEqual(self.app.collection_var.get(), "Custom")
        self.assertTrue(self.app.restore_collection_button.instate(["disabled"]))

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
        self.app.collection_var.set("Custom")
        self.app.select_collection()
        self.assertTrue(self.app.restore_collection_button.instate(["disabled"]))

    def test_collection_is_saved_with_current_urls(self):
        self.app.collection_var.set("GitHub Releases")
        self.app.select_collection()
        self.assertTrue(self.app.save_settings())
        saved = SpeedTestConfig.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        self.assertEqual(saved.collection_id, "github")
        self.assertEqual(saved.urls, BUILTIN_COLLECTIONS[2].urls)

    def test_edited_builtin_urls_persist_with_source_collection_and_restore(self):
        for collection in BUILTIN_COLLECTIONS:
            with self.subTest(collection=collection.id):
                self.app.collection_var.set(collection.label)
                self.app.select_collection()
                edited_url = f"https://example.com/{collection.id}-custom.bin"
                self.app.urls_text.delete("1.0", "end")
                self.app.urls_text.insert("1.0", edited_url)
                self.root.update()
                self.assertIn(f"已编辑 {collection.label} 地址", self.app.settings_message_var.get())
                self.assertTrue(self.app.save_settings())
                self.assertEqual(self.app.collection_var.get(), collection.label)
                self.assertIn(f"已保存 {collection.label} 自定义地址", self.app.settings_message_var.get())

                self.reopen()
                self.assertEqual(self.app.collection_var.get(), collection.label)
                self.assertEqual(self.app.urls_text.get("1.0", "end").strip().splitlines(), [edited_url])
                self.app.restore_collection()
                self.assertEqual(
                    self.app.urls_text.get("1.0", "end").strip().splitlines(),
                    list(COLLECTIONS_BY_LABEL[collection.label].urls),
                )

    def test_switching_collections_keeps_edited_urls_until_restore(self):
        edited_url = "https://example.com/apple-custom.bin"
        self.app.urls_text.delete("1.0", "end")
        self.app.urls_text.insert("1.0", edited_url)
        self.assertTrue(self.app.save_settings())

        self.app.collection_var.set("GitHub Releases")
        self.app.select_collection()
        self.assertEqual(
            self.app.urls_text.get("1.0", "end").strip().splitlines(),
            list(BUILTIN_COLLECTIONS[2].urls),
        )

        self.app.collection_var.set("Apple CDN")
        self.app.select_collection()
        self.assertEqual(self.app.urls_text.get("1.0", "end").strip().splitlines(), [edited_url])

        self.app.restore_collection()
        self.assertEqual(
            self.app.urls_text.get("1.0", "end").strip().splitlines(),
            list(BUILTIN_COLLECTIONS[0].urls),
        )
        self.app.collection_var.set("GitHub Releases")
        self.app.select_collection()
        self.app.collection_var.set("Apple CDN")
        self.app.select_collection()
        self.assertEqual(
            self.app.urls_text.get("1.0", "end").strip().splitlines(),
            list(BUILTIN_COLLECTIONS[0].urls),
        )

    def test_custom_urls_persist_after_restart(self):
        edited_url = "https://example.com/custom.bin"
        self.app.collection_var.set("Custom")
        self.app.select_collection()
        self.app.urls_text.delete("1.0", "end")
        self.app.urls_text.insert("1.0", edited_url)
        self.assertTrue(self.app.save_settings())
        self.reopen()
        self.assertEqual(self.app.collection_var.get(), "Custom")
        self.assertEqual(self.app.urls_text.get("1.0", "end").strip().splitlines(), [edited_url])

    def test_save_requires_successful_readback_verification(self):
        self.app.urls_text.delete("1.0", "end")
        self.app.urls_text.insert("1.0", "https://example.com/verified.bin")
        with patch("speedtest_gui.app.load_config", return_value=(SpeedTestConfig(), "")), patch(
            "speedtest_gui.app.messagebox.showerror"
        ) as showerror:
            self.assertFalse(self.app.save_settings())
        showerror.assert_called_once_with("保存失败", "保存后校验失败，请重新保存。", parent=self.root)
        self.assertNotIn("已保存", self.app.settings_message_var.get())

    def test_invalid_settings_select_settings_page(self):
        self.app.connections_var.set("bad")
        with patch("speedtest_gui.app.messagebox.showerror") as showerror:
            self.assertFalse(self.app.save_settings())
            showerror.assert_called_once()
        self.assertEqual(self.app.notebook.select(), str(self.app.settings_page))
        self.assertFalse(self.path.exists())

    def test_invalid_urls_show_validation_errors(self):
        for url, expected in (
            ("", "请至少填写一个下载地址。"),
            ("file:///C:/sample.bin", "第 1 行须填写有效的 HTTP 或 HTTPS 地址。"),
            ("https://example.com:99999/file.bin", "第 1 行须填写有效的 HTTP 或 HTTPS 地址。"),
        ):
            with self.subTest(url=url):
                self.app.urls_text.delete("1.0", "end")
                self.app.urls_text.insert("1.0", url)
                with patch("speedtest_gui.app.messagebox.showerror") as showerror:
                    self.assertFalse(self.app.save_settings())
                showerror.assert_called_once_with("设置有误", expected, parent=self.root)

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
