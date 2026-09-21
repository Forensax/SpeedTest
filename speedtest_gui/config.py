"""Validated application settings. Proxy passwords are never serialized."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit


APPLE_URLS = (
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/093-39795/6A1A4D22-A7DC-4C2C-A147-D4D9D4EB7D1F/iPhone18,4_26.0_23A341_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/089-02043/59201911-1F46-4505-A090-20ED7B1239F2/iPhone18,4_26.1_23B82_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/093-44415/65359B2E-9997-4686-B335-CEEA4524A334/iPhone18,4_26.0.1_23A355_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/089-14211/A9EC7D63-0F1D-49B9-A57B-1D0C85EE98F8/iPhone18,4_26.1_23B85_Restore.ipsw",
    "https://updates.cdn-apple.com/2025FallFCS/fullrestores/089-02032/9FC73E4F-1EE3-452D-A18C-117484A33863/iPhone17,1_26.1_23B82_Restore.ipsw",
    "https://updates.cdn-apple.com/2025SummerFCS/fullrestores/093-21361/F0033AA8-4016-4030-BA3A-5C6E3E1FE7D4/iPhone17,1_18.6.2_22G100_Restore.ipsw",
    "https://updates.cdn-apple.com/2025WinterFCS/fullrestores/072-68203/37420C35-16EC-4466-850B-8F755C43FC73/iPhone17,1_18.3_22D63_Restore.ipsw",
    "https://updates.cdn-apple.com/2024FallFCS/fullrestores/062-77773/01645362-ACAC-4B29-BF73-A5397FA1034A/iPhone17,1_18.0_22A3354_Restore.ipsw",
)


@dataclass(frozen=True)
class DownloadCollection:
    """A named group of download URLs shown in the settings page."""

    id: str
    label: str
    urls: tuple[str, ...]


HUGGINGFACE_URLS = (
    "https://huggingface.co/Qwen/Qwen3-8B/resolve/main/model-00001-of-00005.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-8B/resolve/main/model-00002-of-00005.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-8B/resolve/main/model-00003-of-00005.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-8B/resolve/main/model-00004-of-00005.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-8B/resolve/main/model-00005-of-00005.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-Embedding-8B/resolve/main/model-00001-of-00004.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-Embedding-8B/resolve/main/model-00002-of-00004.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-Embedding-8B/resolve/main/model-00003-of-00004.safetensors?download=true",
    "https://huggingface.co/Qwen/Qwen3-Embedding-8B/resolve/main/model-00004-of-00004.safetensors?download=true",
)


GITHUB_URLS = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-17-13-19/ffmpeg-N-126626-g7070fe638e-win64-gpl.zip",
    "https://github.com/obsproject/obs-studio/releases/download/32.2.2/OBS-Studio-32.2.2-Windows-x64.zip",
)


DEFAULT_COLLECTION_ID = "apple"
CUSTOM_COLLECTION_ID = "custom"
BUILTIN_COLLECTIONS = (
    DownloadCollection("apple", "Apple CDN", APPLE_URLS),
    DownloadCollection("huggingface", "Hugging Face Models", HUGGINGFACE_URLS),
    DownloadCollection("github", "GitHub Releases", GITHUB_URLS),
)
CUSTOM_COLLECTION = DownloadCollection(CUSTOM_COLLECTION_ID, "自定义", ())
COLLECTIONS = BUILTIN_COLLECTIONS + (CUSTOM_COLLECTION,)
COLLECTIONS_BY_ID = {collection.id: collection for collection in COLLECTIONS}
COLLECTIONS_BY_LABEL = {collection.label: collection for collection in COLLECTIONS}

# Kept as a public compatibility alias for callers using the original name.
DEFAULT_URLS = APPLE_URLS


def get_collection(collection_id: str) -> DownloadCollection:
    try:
        return COLLECTIONS_BY_ID[collection_id]
    except (KeyError, TypeError) as exc:
        raise ConfigError("测速集合无效。") from exc


def collection_id_for_urls(urls: tuple[str, ...] | list[str]) -> str:
    candidate = tuple(urls)
    for collection in BUILTIN_COLLECTIONS:
        if candidate == collection.urls:
            return collection.id
    return CUSTOM_COLLECTION_ID


class ConfigError(ValueError):
    """Settings cannot be used to start a measurement."""


@dataclass(frozen=True)
class ProxyConfig:
    mode: str = "direct"
    host: str = "127.0.0.1"
    port: int = 10808
    username: str = ""
    password: str = field(default="", repr=False)

    def validate(self) -> None:
        if self.mode not in ("direct", "http", "https", "socks5"):
            raise ConfigError("请选择有效的代理类型。")
        if not all(isinstance(value, str) for value in (self.host, self.username, self.password)):
            raise ConfigError("代理地址和认证信息必须是文本。")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ConfigError("代理端口应为 1–65535。")
        if self.mode == "direct":
            return
        host = self.host.strip()
        if not host or any(char.isspace() or char in "/\\@?#" for char in host):
            raise ConfigError("代理地址只填写主机名或 IP。")
        if ":" in host:
            import ipaddress

            try:
                ipaddress.IPv6Address(host.strip("[]"))
            except ValueError as exc:
                raise ConfigError("代理地址无效，端口请单独填写。") from exc
        if self.password and not self.username:
            raise ConfigError("使用代理密码时请填写用户名。")

    def requests_proxies(self) -> dict[str, str]:
        self.validate()
        if self.mode == "direct":
            return {}
        scheme = "socks5h" if self.mode == "socks5" else self.mode
        host = self.host.strip().strip("[]")
        if ":" in host:
            host = f"[{host}]"
        auth = ""
        if self.username:
            auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        address = f"{scheme}://{auth}{host}:{self.port}"
        return {"http": address, "https": address}

    @property
    def label(self) -> str:
        if self.mode == "direct":
            return "直连"
        return f"{self.mode.upper()} · {self.host}:{self.port}"


@dataclass(frozen=True)
class SpeedTestConfig:
    urls: tuple[str, ...] = DEFAULT_URLS
    connections: int = 8
    duration_seconds: int = 0
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    collection_id: str = DEFAULT_COLLECTION_ID

    def validate(self) -> None:
        get_collection(self.collection_id)
        if type(self.connections) is not int or not 1 <= self.connections <= 64:
            raise ConfigError("并发连接数应为 1–64。")
        if type(self.duration_seconds) is not int or not 0 <= self.duration_seconds <= 86400:
            raise ConfigError("测速时长应为 1–86400 秒；0 表示持续测速。")
        if not isinstance(self.urls, (tuple, list)) or not self.urls:
            raise ConfigError("请至少填写一个下载地址。")
        for index, url in enumerate(self.urls, 1):
            if not isinstance(url, str) or not url or any(char.isspace() for char in url):
                raise ConfigError(f"第 {index} 行下载地址无效。")
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise ValueError("unsupported URL")
                if parsed.port is not None and not 1 <= parsed.port <= 65535:
                    raise ValueError("invalid port")
            except ValueError as exc:
                raise ConfigError(f"第 {index} 行须填写有效的 HTTP 或 HTTPS 地址。") from exc
        if not isinstance(self.proxy, ProxyConfig):
            raise ConfigError("代理配置无效。")
        self.proxy.validate()

    def to_dict(self) -> dict:
        self.validate()
        return {
            "version": 2,
            "collection_id": self.collection_id,
            "urls": list(self.urls),
            "connections": self.connections,
            "duration_seconds": self.duration_seconds,
            "proxy": {
                "mode": self.proxy.mode,
                "host": self.proxy.host,
                "port": self.proxy.port,
                "username": self.proxy.username,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> SpeedTestConfig:
        if not isinstance(data, dict) or data.get("version", 1) not in (1, 2):
            raise ConfigError("配置格式不受支持。")
        proxy = data.get("proxy", {})
        if not isinstance(proxy, dict):
            raise ConfigError("代理配置无效。")
        version = data.get("version", 1)
        collection_id = data.get("collection_id") if version == 2 else None
        if collection_id is not None:
            get_collection(collection_id)
        raw_urls = data.get("urls", list(DEFAULT_URLS))
        if raw_urls is None or raw_urls == []:
            selected = get_collection(collection_id or DEFAULT_COLLECTION_ID)
            raw_urls = list(selected.urls or DEFAULT_URLS)
        if not isinstance(raw_urls, list):
            raise ConfigError("下载地址应为列表。")
        urls = tuple(raw_urls)
        if collection_id is None:
            collection_id = collection_id_for_urls(urls)
        result = cls(
            urls=urls,
            connections=data.get("connections", 8),
            duration_seconds=data.get("duration_seconds", 0),
            proxy=ProxyConfig(
                mode=proxy.get("mode", "direct"),
                host=proxy.get("host", "127.0.0.1"),
                port=proxy.get("port", 10808),
                username=proxy.get("username", ""),
            ),
            collection_id=collection_id,
        )
        result.validate()
        return result


def default_config_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return base / "SpeedTest" / "config.json"


def load_config(path: Path | None = None) -> tuple[SpeedTestConfig, str]:
    path = path or default_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SpeedTestConfig.from_dict(data), ""
    except FileNotFoundError:
        return SpeedTestConfig(), ""
    except (OSError, ValueError, TypeError, UnicodeError):
        return SpeedTestConfig(), "配置读取失败，已恢复默认设置。"


def save_config(config: SpeedTestConfig, path: Path | None = None) -> None:
    """Atomically replace settings without persisting the proxy password."""
    serialized = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix="config-", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(serialized)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
