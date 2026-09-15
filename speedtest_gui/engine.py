"""Threaded streaming downloads with cancellable retries and UI-safe snapshots."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError as UrllibHTTPError

from . import __version__
from .config import SpeedTestConfig


CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 2.0
RETRY_SECONDS = 5.0


class TestState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    FINISHED = "finished"


@dataclass(frozen=True)
class SpeedSample:
    elapsed_seconds: float
    bytes_per_second: float


@dataclass(frozen=True)
class SpeedSnapshot:
    state: TestState
    total_bytes: int
    elapsed_seconds: float
    bytes_per_second: float
    average_bytes_per_second: float
    peak_bytes_per_second: float
    active_connections: int
    connections: int
    samples: tuple[SpeedSample, ...]
    last_error: str
    error_count: int
    stop_reason: str

    @property
    def busy(self) -> bool:
        return self.state in (TestState.RUNNING, TestState.STOPPING)

    @property
    def mbps(self) -> float:
        return self.bytes_per_second * 8 / 1_000_000

    @property
    def megabytes_per_second(self) -> float:
        return self.bytes_per_second / 1_000_000


class TransferMeter:
    """Caller holds the engine lock; timestamps are monotonic seconds."""

    def __init__(self, started_at: float):
        self.started_at = started_at
        self.total_bytes = 0
        self.last_sample_at = started_at
        self.last_sample_bytes = 0
        self.bytes_per_second = 0.0
        self.peak_bytes_per_second = 0.0
        self.samples: deque[SpeedSample] = deque(maxlen=62)

    def sample(self, now: float, *, force: bool = False) -> None:
        interval = now - self.last_sample_at
        if interval <= 0 or (interval < 1.0 and not force):
            return
        self.bytes_per_second = (self.total_bytes - self.last_sample_bytes) / interval
        self.peak_bytes_per_second = max(self.peak_bytes_per_second, self.bytes_per_second)
        self.samples.append(SpeedSample(now - self.started_at, self.bytes_per_second))
        self.last_sample_at = now
        self.last_sample_bytes = self.total_bytes
        while self.samples and self.samples[0].elapsed_seconds < now - self.started_at - 60:
            self.samples.popleft()


class SpeedTestEngine:
    def __init__(self, *, clock: Callable[[], float] = time.perf_counter):
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._state = TestState.IDLE
        self._config = SpeedTestConfig()
        self._meter = TransferMeter(clock())
        self._stopped_at: float | None = None
        self._active_connections = 0
        self._last_error = ""
        self._error_count = 0
        self._stop_reason = ""

    def start(self, config: SpeedTestConfig) -> None:
        config.validate()
        # Take an immutable copy, including when API callers supply a list of URLs.
        config = SpeedTestConfig(tuple(config.urls), config.connections, config.duration_seconds, config.proxy)
        with self._lock:
            if self._state in (TestState.RUNNING, TestState.STOPPING) or (
                self._supervisor is not None and self._supervisor.is_alive()
            ):
                raise RuntimeError("请等待当前测速停止。")
            self._config = config
            self._stop_event = threading.Event()
            self._meter = TransferMeter(self._clock())
            self._stopped_at = None
            self._active_connections = 0
            self._last_error = ""
            self._error_count = 0
            self._stop_reason = ""
            self._state = TestState.RUNNING
            self._supervisor = threading.Thread(target=self._run, name="SpeedTest-monitor", daemon=True)
            try:
                self._supervisor.start()
            except RuntimeError:
                self._state = TestState.IDLE
                self._supervisor = None
                raise

    def stop(self, reason: str = "manual") -> None:
        with self._lock:
            if self._state != TestState.RUNNING:
                return
            self._stopped_at = self._clock()
            self._meter.sample(self._stopped_at, force=True)
            self._stop_reason = reason
            self._state = TestState.STOPPING
            self._stop_event.set()

    def wait(self, timeout: float | None = None) -> bool:
        supervisor = self._supervisor
        if supervisor is None:
            return True
        supervisor.join(timeout)
        return not supervisor.is_alive()

    def snapshot(self) -> SpeedSnapshot:
        with self._lock:
            now = self._stopped_at if self._stopped_at is not None else self._clock()
            elapsed = max(0.0, now - self._meter.started_at) if self._state != TestState.IDLE else 0.0
            return SpeedSnapshot(
                state=self._state,
                total_bytes=self._meter.total_bytes,
                elapsed_seconds=elapsed,
                bytes_per_second=self._meter.bytes_per_second,
                average_bytes_per_second=self._meter.total_bytes / elapsed if elapsed else 0.0,
                peak_bytes_per_second=self._meter.peak_bytes_per_second,
                active_connections=self._active_connections,
                connections=self._config.connections,
                samples=tuple(self._meter.samples),
                last_error=self._last_error,
                error_count=self._error_count,
                stop_reason=self._stop_reason,
            )

    def _run(self) -> None:
        workers: list[threading.Thread] = []
        try:
            for index in range(self._config.connections):
                if self._stop_event.is_set():
                    break
                worker = threading.Thread(
                    target=self._download,
                    args=(self._config.urls[index % len(self._config.urls)],),
                    name=f"SpeedTest-download-{index + 1}",
                    daemon=True,
                )
                worker.start()
                workers.append(worker)
            while not self._stop_event.wait(0.05):
                with self._lock:
                    now = self._clock()
                    self._meter.sample(now)
                    duration = self._config.duration_seconds
                    if duration and now - self._meter.started_at >= duration:
                        self.stop("timer")
                if workers and not any(worker.is_alive() for worker in workers):
                    self.stop("error")
        except Exception:
            self._record_error("下载线程启动失败")
            self.stop("error")
        finally:
            self.stop("error")
            for worker in workers:
                worker.join()
            with self._lock:
                self._active_connections = 0
                self._state = TestState.FINISHED

    def _record_error(self, message: str) -> None:
        with self._lock:
            if not self._stop_event.is_set():
                self._last_error = message
                self._error_count += 1

    @staticmethod
    def _error_message(error: Exception, url: str) -> str:
        # Exception strings may contain proxy credentials or signed URLs.
        host = urlsplit(url).hostname or "下载地址"
        if isinstance(error, requests.exceptions.ProxyError):
            return "代理连接失败"
        if isinstance(error, requests.exceptions.SSLError):
            return f"证书验证失败 · {host}"
        if isinstance(error, requests.exceptions.HTTPError):
            code = error.response.status_code if error.response is not None else "错误"
            return f"HTTP {code} · {host}"
        if isinstance(error, requests.exceptions.Timeout):
            return f"连接超时 · {host}"
        if isinstance(error, (UrllibHTTPError, requests.exceptions.ConnectionError)):
            return f"连接中断或读取超时 · {host}"
        return f"下载失败 · {host}"

    def _download(self, url: str) -> None:
        try:
            with requests.Session() as session:
                session.trust_env = False
                session.proxies = self._config.proxy.requests_proxies()
                session.headers.update({
                    "User-Agent": f"SpeedTest/{__version__}",
                    "Accept-Encoding": "identity",
                    "Cache-Control": "no-cache",
                })
                while not self._stop_event.is_set():
                    try:
                        with session.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as response:
                            response.raise_for_status()
                            if self._stop_event.is_set():
                                break
                            with self._lock:
                                self._active_connections += 1
                            received = 0
                            try:
                                while not self._stop_event.is_set():
                                    # read1 returns available data instead of waiting for a
                                    # full chunk, keeping slow streams measurable/cancellable.
                                    chunk = response.raw.read1(CHUNK_SIZE, decode_content=False)
                                    if not chunk:
                                        break
                                    with self._lock:
                                        if not self._stop_event.is_set():
                                            received += len(chunk)
                                            self._meter.total_bytes += len(chunk)
                            finally:
                                with self._lock:
                                    self._active_connections -= 1
                            if not received and not self._stop_event.is_set():
                                self._record_error(f"地址未返回数据 · {urlsplit(url).hostname}")
                                self._stop_event.wait(RETRY_SECONDS)
                    except (requests.RequestException, UrllibHTTPError, OSError) as error:
                        self._record_error(self._error_message(error, url))
                        self._stop_event.wait(RETRY_SECONDS)
                    except Exception:
                        self._record_error(f"下载失败 · {urlsplit(url).hostname}")
                        self._stop_event.wait(RETRY_SECONDS)
        except Exception:
            self._record_error("下载连接初始化失败")
