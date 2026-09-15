"""Build the portable executable, test its startup, then write its checksum."""

import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from speedtest_gui import __version__


def main():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SystemExit("Use Windows x64 and 64-bit Python to build SpeedTest.exe.")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if os.environ.get("GITHUB_REF_TYPE") == "tag" and tag != f"v{__version__}":
        raise SystemExit(f"Release tag {tag!r} does not match application version {__version__}.")
    version_file = (ROOT / "assets" / "version_info.txt").read_text(encoding="utf-8")
    if f"StringStruct('FileVersion', '{__version__}')" not in version_file:
        raise SystemExit("Windows version metadata must match the application version.")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(ROOT / "SpeedTest.spec")], cwd=ROOT, check=True)
    executable = ROOT / "dist" / "SpeedTest.exe"
    smoke_report = ROOT / "build" / "smoke-report.json"
    smoke_report.unlink(missing_ok=True)
    subprocess.run([str(executable), "--smoke-test", str(smoke_report)], cwd=ROOT, check=True, timeout=45)
    report = json.loads(smoke_report.read_text(encoding="utf-8"))
    if not report.get("ok") or not report.get("frozen") or report.get("tabs") != ["测速", "设置"]:
        raise SystemExit(f"Executable startup test failed: {report}")
    checksum = hashlib.sha256(executable.read_bytes()).hexdigest()
    (ROOT / "dist" / "SHA256SUMS.txt").write_text(f"{checksum}  SpeedTest.exe\n", encoding="utf-8")
    print(f"Built {executable.name}: {executable.stat().st_size / 1_000_000:.2f} MB")
    print(f"SHA-256: {checksum}")
    print("Executable startup test passed.")


if __name__ == "__main__":
    main()
