import ast
import json
import tempfile
import unittest
from pathlib import Path

from speedtest_gui.config import DEFAULT_URLS, ConfigError, ProxyConfig, SpeedTestConfig, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_defaults_match_original_script(self):
        script = Path(__file__).resolve().parents[1] / "spd.py"
        tree = ast.parse(script.read_text(encoding="utf-8-sig"))
        urls = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "URLS" for target in node.targets))
        self.assertEqual(tuple(urls), DEFAULT_URLS)
        SpeedTestConfig().validate()

    def test_save_reload_never_persists_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings" / "config.json"
            config = SpeedTestConfig(connections=3, duration_seconds=30, proxy=ProxyConfig("socks5", "localhost", 10808, "alice", "session-secret"))
            save_config(config, path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("session-secret", text)
            self.assertNotIn("password", text)
            loaded, warning = load_config(path)
            self.assertEqual(warning, "")
            self.assertEqual(loaded.connections, 3)
            self.assertEqual(loaded.duration_seconds, 30)
            self.assertEqual(loaded.proxy.username, "alice")
            self.assertEqual(loaded.proxy.password, "")
            self.assertEqual(len(list(path.parent.glob("*.tmp"))), 0)
            self.assertNotIn("session-secret", repr(config))

    def test_corrupted_and_missing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(load_config(path), (SpeedTestConfig(), ""))
            for content in ("{broken", "[]", json.dumps({"connections": "8"}), json.dumps({"proxy": None}), json.dumps({"urls": "http://example.com"})):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    config, warning = load_config(path)
                    self.assertEqual(config, SpeedTestConfig())
                    self.assertTrue(warning)

    def test_invalid_inputs(self):
        for config in (
            SpeedTestConfig(urls=()),
            SpeedTestConfig(urls=("file:///C:/sample.bin",)),
            SpeedTestConfig(urls=("https://",)),
            SpeedTestConfig(urls=("http://example.com:99999/file",)),
            SpeedTestConfig(urls=("http://bad host/file",)),
            SpeedTestConfig(connections=0),
            SpeedTestConfig(connections=65),
            SpeedTestConfig(connections=True),
            SpeedTestConfig(duration_seconds=-1),
            SpeedTestConfig(proxy=ProxyConfig("http", "http://localhost", 10808)),
            SpeedTestConfig(proxy=ProxyConfig("http", "localhost", 0)),
        ):
            with self.subTest(config=config), self.assertRaises(ConfigError):
                config.validate()

    def test_proxy_protocols_and_escaped_credentials(self):
        self.assertEqual(ProxyConfig().requests_proxies(), {})
        for mode, scheme in (("http", "http"), ("https", "https"), ("socks5", "socks5h")):
            with self.subTest(mode=mode):
                proxy = ProxyConfig(mode, "localhost", 10808, "u@ser", "p:a/ss").requests_proxies()
                expected = f"{scheme}://u%40ser:p%3Aa%2Fss@localhost:10808"
                self.assertEqual(proxy, {"http": expected, "https": expected})

    def test_ipv6_proxy(self):
        self.assertEqual(ProxyConfig("http", "::1", 10808).requests_proxies()["https"], "http://[::1]:10808")

    def test_legacy_password_is_not_loaded(self):
        config = SpeedTestConfig.from_dict({"proxy": {"mode": "http", "username": "alice", "password": "old-secret"}})
        self.assertEqual(config.proxy.password, "")
