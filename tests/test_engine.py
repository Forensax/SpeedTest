import base64
import os
import socket
import time
import unittest
from unittest.mock import patch

from speedtest_gui.config import ProxyConfig, SpeedTestConfig
from speedtest_gui.engine import SpeedTestEngine, TestState, ThreadState, TransferMeter
from tests.servers import HTTPFixture, SocksFixture


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class MeterTests(unittest.TestCase):
    def test_real_intervals_and_peak(self):
        meter = TransferMeter(10)
        meter.total_bytes = 2_000_000
        meter.sample(12)
        self.assertEqual(meter.bytes_per_second, 1_000_000)
        self.assertEqual(meter.peak_bytes_per_second, 1_000_000)
        meter.total_bytes = 2_500_000
        meter.sample(14)
        self.assertEqual(meter.bytes_per_second, 250_000)
        self.assertEqual(meter.peak_bytes_per_second, 1_000_000)
        self.assertEqual(meter.samples[-1].elapsed_seconds, 4)
        meter.sample(14, force=True)
        self.assertEqual(meter.bytes_per_second, 250_000)

    def test_history_is_limited_to_sixty_seconds(self):
        meter = TransferMeter(0)
        for second in range(1, 181):
            meter.total_bytes += 1000
            meter.sample(second)
        self.assertTrue(all(sample.elapsed_seconds >= 120 for sample in meter.samples))
        self.assertLessEqual(len(meter.samples), 61)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = SpeedTestEngine()

    def tearDown(self):
        self.engine.stop()
        self.assertTrue(self.engine.wait(6), "download threads did not stop")

    def test_parallel_download_and_clean_restart(self):
        with HTTPFixture() as server:
            config = SpeedTestConfig(urls=(server.url + "/stream", server.url + "/slow"), connections=4)
            self.engine.start(config)
            self.assertTrue(wait_until(lambda: self.engine.snapshot().active_connections == 4))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().total_bytes > 32768))
            self.engine.stop()
            self.assertTrue(self.engine.wait(3))
            first = self.engine.snapshot()
            self.assertEqual(first.state, TestState.FINISHED)
            self.assertEqual(first.active_connections, 0)
            self.assertEqual(first.stop_reason, "manual")
            time.sleep(0.05)
            self.assertEqual(first.total_bytes, self.engine.snapshot().total_bytes)
            paths = [path for path, _headers in server.requests]
            self.assertEqual(paths.count("/stream"), 2)
            self.assertEqual(paths.count("/slow"), 2)
            self.engine.start(SpeedTestConfig(urls=(server.url + "/empty",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().error_count > 0))
            self.assertEqual(self.engine.snapshot().total_bytes, 0)
            self.engine.stop()
            self.assertTrue(self.engine.wait(3))

    def test_thread_details_track_assigned_urls_and_individual_totals(self):
        with HTTPFixture() as server:
            urls = (server.url + "/stream", server.url + "/slow")
            self.engine.start(SpeedTestConfig(urls=urls, connections=4))
            expected_urls = [urls[index % len(urls)] for index in range(4)]
            self.assertTrue(wait_until(lambda: len(self.engine.snapshot().thread_details) == 4))
            self.assertTrue(
                wait_until(
                    lambda: all(detail.total_bytes > 0 for detail in self.engine.snapshot().thread_details)
                )
            )
            snapshot = self.engine.snapshot()
            self.assertEqual([detail.index for detail in snapshot.thread_details], [1, 2, 3, 4])
            self.assertEqual([detail.url for detail in snapshot.thread_details], expected_urls)
            self.assertEqual(
                sum(detail.total_bytes for detail in snapshot.thread_details),
                snapshot.total_bytes,
            )
            self.assertTrue(all(detail.bytes_per_second >= 0 for detail in snapshot.thread_details))
            self.engine.stop()
            self.assertTrue(self.engine.wait(3))
            final = self.engine.snapshot()
            self.assertTrue(all(detail.state == ThreadState.STOPPED for detail in final.thread_details))

    def test_timer_and_decimal_units(self):
        with HTTPFixture() as server:
            self.engine.start(SpeedTestConfig(urls=(server.url + "/stream",), connections=2, duration_seconds=1))
            self.assertTrue(self.engine.wait(4))
            snapshot = self.engine.snapshot()
            self.assertEqual(snapshot.stop_reason, "timer")
            self.assertGreater(snapshot.total_bytes, 0)
            self.assertGreaterEqual(snapshot.elapsed_seconds, 1)
            self.assertLess(snapshot.elapsed_seconds, 1.6)
            self.assertAlmostEqual(snapshot.mbps, snapshot.megabytes_per_second * 8)
            self.assertAlmostEqual(snapshot.average_bytes_per_second, snapshot.total_bytes / snapshot.elapsed_seconds)

    def test_stop_during_slow_drip_does_not_wait_for_full_chunk(self):
        with HTTPFixture() as server:
            self.engine.start(SpeedTestConfig(urls=(server.url + "/slow",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().total_bytes > 0))
            started = time.monotonic()
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))
            self.assertLess(time.monotonic() - started, 1)

    def test_stalled_read_and_restart_guard(self):
        with HTTPFixture() as server:
            config = SpeedTestConfig(urls=(server.url + "/stall",), connections=1)
            self.engine.start(config)
            self.assertTrue(server.stalled.wait(2))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().active_connections == 1))
            started = time.monotonic()
            self.engine.stop()
            self.assertEqual(self.engine.snapshot().state, TestState.STOPPING)
            with self.assertRaises(RuntimeError):
                self.engine.start(config)
            self.assertTrue(self.engine.wait(3))
            self.assertLess(time.monotonic() - started, 3)

    def test_http_error_and_cancellable_retry(self):
        with HTTPFixture() as server:
            self.engine.start(SpeedTestConfig(urls=(server.url + "/error",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().error_count == 1))
            self.assertIn("HTTP 503", self.engine.snapshot().last_error)
            self.engine.stop()
            self.assertTrue(self.engine.wait(1), "stop must interrupt the five-second retry delay")

    def test_read_timeout_is_reported(self):
        with HTTPFixture() as server:
            self.engine.start(SpeedTestConfig(urls=(server.url + "/stall",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().error_count > 0, timeout=3))
            self.assertIn("超时", self.engine.snapshot().last_error)
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))

    def test_empty_body_does_not_busy_loop(self):
        with HTTPFixture() as server:
            self.engine.start(SpeedTestConfig(urls=(server.url + "/empty",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().error_count > 0))
            time.sleep(0.15)
            self.assertEqual(len(server.requests), 1)
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))

    def test_completed_download_restarts(self):
        with HTTPFixture() as server:
            self.engine.start(SpeedTestConfig(urls=(server.url + "/finite",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().total_bytes >= 65536))
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))
            self.assertGreaterEqual(len(server.requests), 2)

    def test_direct_mode_ignores_environment_proxies(self):
        with HTTPFixture() as server, patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
            self.engine.start(SpeedTestConfig(urls=(server.url + "/stream",), connections=1))
            self.assertTrue(wait_until(lambda: self.engine.snapshot().total_bytes > 0))
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))

    def test_http_proxy_and_basic_auth_are_used(self):
        with HTTPFixture() as proxy:
            config = SpeedTestConfig(urls=("http://speedtest.invalid/stream",), connections=1, proxy=ProxyConfig("http", "127.0.0.1", proxy.port, "alice", "a:b/@"))
            self.engine.start(config)
            self.assertTrue(wait_until(lambda: self.engine.snapshot().total_bytes > 0))
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))
            path, headers = proxy.requests[0]
            self.assertEqual(path, "http://speedtest.invalid/stream")
            self.assertEqual(headers["Proxy-Authorization"], "Basic " + base64.b64encode(b"alice:a:b/@").decode())

    def test_socks5_remote_dns_and_authentication(self):
        with SocksFixture(authenticated=True) as proxy:
            config = SpeedTestConfig(urls=("http://speedtest.invalid/stream",), connections=1, proxy=ProxyConfig("socks5", "127.0.0.1", proxy.port, "alice", "p:@/ss"))
            self.engine.start(config)
            self.assertTrue(wait_until(lambda: self.engine.snapshot().total_bytes > 0))
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))
            self.assertEqual(proxy.destinations[0], (3, "speedtest.invalid", 80))
            self.assertEqual(proxy.credentials[0], ("alice", "p:@/ss"))

    def test_unreachable_proxy_reports_safe_error(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
            config = SpeedTestConfig(urls=("https://speedtest.invalid/file",), connections=1, proxy=ProxyConfig("http", "127.0.0.1", port, "alice", "secret-password"))
            self.engine.start(config)
            self.assertTrue(wait_until(lambda: self.engine.snapshot().error_count > 0, timeout=5))
            snapshot = self.engine.snapshot()
            self.assertEqual(snapshot.last_error, "代理连接失败")
            self.assertNotIn("secret-password", repr(snapshot))
            self.engine.stop()
            self.assertTrue(self.engine.wait(1))
