"""Optional, traffic-generating integration check using the local 10808 proxy."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from speedtest_gui.config import DEFAULT_URLS, ProxyConfig, SpeedTestConfig
from speedtest_gui.engine import SpeedTestEngine


def main():
    results = []
    for mode in ("direct", "http", "socks5"):
        engine = SpeedTestEngine()
        config = SpeedTestConfig(urls=(DEFAULT_URLS[0],), connections=1, duration_seconds=3, proxy=ProxyConfig(mode=mode))
        engine.start(config)
        stopped = engine.wait(15)
        if not stopped:
            engine.stop()
            stopped = engine.wait(7)
        snapshot = engine.snapshot()
        result = {
            "mode": mode,
            "bytes": snapshot.total_bytes,
            "seconds": round(snapshot.elapsed_seconds, 3),
            "average_mbps": round(snapshot.average_bytes_per_second * 8 / 1_000_000, 3),
            "active_connections": snapshot.active_connections,
            "error": snapshot.last_error,
            "passed": stopped and snapshot.total_bytes > 0 and snapshot.active_connections == 0,
        }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    output = ROOT / "artifacts" / "network-check.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
