"""Compact two-page Tk interface. All widget access stays on the main thread."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import __version__
from .config import (
    COLLECTIONS,
    COLLECTIONS_BY_LABEL,
    CUSTOM_COLLECTION_ID,
    ConfigError,
    ProxyConfig,
    SpeedTestConfig,
    get_collection,
    load_config,
    save_config,
)
from .engine import SpeedSnapshot, SpeedTestEngine, TestState


PROXY_MODES = {"直连": "direct", "HTTP": "http", "HTTPS": "https", "SOCKS5": "socks5"}
BLUE = "#2563eb"
INK = "#182537"
MUTED = "#6b7789"
BORDER = "#e2e7ee"
NOTEBOOK_TAB_WIDTH = 8
NOTEBOOK_TAB_PADDING = (18, 8)


def format_bytes(value: int) -> str:
    for unit, size in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if value >= size:
            return f"{value / size:,.2f} {unit}"
    return f"{value} B"


def format_duration(seconds: float) -> str:
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def asset_path(filename: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / filename


class SpeedTestApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        config: SpeedTestConfig | None = None,
        config_path: Path | None = None,
        engine: SpeedTestEngine | None = None,
    ):
        self.root = root
        self.config_path = config_path
        self.config, self._warning = (config, "") if config is not None else load_config(config_path)
        self.engine = engine or SpeedTestEngine()
        self._closing = False
        self._after_id: str | None = None
        self._last_busy: bool | None = None
        self._last_chart_samples = None
        self._last_snapshot = self.engine.snapshot()
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)

        root.title("SpeedTest")
        root.geometry(f"{round(760 * self.scale)}x{round(550 * self.scale)}")
        root.minsize(round(700 * self.scale), round(510 * self.scale))
        root.configure(background="white")
        root.protocol("WM_DELETE_WINDOW", self.close)
        if sys.platform == "win32" and asset_path("speedtest.ico").exists():
            root.iconbitmap(str(asset_path("speedtest.ico")))
        self._style()
        self._variables()
        self._build()
        self._fill_settings()
        self._tick()

    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        font = ("Microsoft YaHei UI", 10)
        self.root.option_add("*Font", font)
        style.configure(".", font=font, background="white", foreground=INK)
        style.configure("TFrame", background="white")
        style.configure("TLabel", background="white")
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("TNotebook", background="white", borderwidth=0, tabmargins=(16, 0, 16, 0))
        style.configure("TNotebook.Tab", padding=NOTEBOOK_TAB_PADDING, width=NOTEBOOK_TAB_WIDTH, background="white", borderwidth=0)
        style.map(
            "TNotebook.Tab",
            padding=[("selected", NOTEBOOK_TAB_PADDING), ("!selected", NOTEBOOK_TAB_PADDING)],
            background=[("selected", "#edf3ff")],
            foreground=[("disabled", "#a6afbc"), ("selected", BLUE)],
        )
        common = dict(font=font, padding=(14, 7), borderwidth=1, relief="flat", anchor="center")
        style.configure("App.TButton", **common, background="white", foreground=INK, bordercolor="#cfd7e2", focuscolor=BLUE)
        style.map("App.TButton", background=[("disabled", "#f7f8fa"), ("pressed", "#e6ecf5"), ("active", "#f1f5fb")], foreground=[("disabled", "#a4adba")])
        style.configure("Primary.TButton", **common, background=BLUE, foreground="white", bordercolor=BLUE, focuscolor="white")
        style.map("Primary.TButton", background=[("disabled", "#dce5f5"), ("pressed", "#1e40af"), ("active", "#1d4ed8")], foreground=[("disabled", "#8b9ab1")])
        style.configure("TEntry", padding=5, fieldbackground="white", bordercolor="#cfd7e2", lightcolor="white", darkcolor="white")
        style.configure("TCombobox", padding=5, fieldbackground="white", background="white", bordercolor="#cfd7e2", arrowsize=12)
        style.map("TCombobox", fieldbackground=[("readonly", "white"), ("disabled", "#f4f6f8")], foreground=[("disabled", "#a4adba")])
        style.configure("TSpinbox", padding=5, fieldbackground="white", bordercolor="#cfd7e2", arrowsize=10)
        style.configure("TCheckbutton", background="white", focuscolor="white")
        style.map("TCheckbutton", background=[("active", "white")])
        style.configure("Horizontal.TSeparator", background=BORDER)

    def _variables(self) -> None:
        self.speed_var = tk.StringVar(value="0.00")
        self.byte_speed_var = tk.StringVar(value="0.00 MB/s")
        self.average_var = tk.StringVar(value="0.00 Mbps")
        self.peak_var = tk.StringVar(value="0.00 Mbps")
        self.total_var = tk.StringVar(value="0 B")
        self.elapsed_var = tk.StringVar(value="00:00")
        self.state_var = tk.StringVar(value="就绪")
        self.route_var = tk.StringVar()
        self.detail_var = tk.StringVar(value=self._warning)
        self.duration_label_var = tk.StringVar(value="持续测速")
        self.collection_var = tk.StringVar()
        self.connections_var = tk.StringVar()
        self.timed_var = tk.BooleanVar()
        self.duration_var = tk.StringVar(value="60")
        self.proxy_mode_var = tk.StringVar()
        self.proxy_host_var = tk.StringVar()
        self.proxy_port_var = tk.StringVar()
        self.proxy_username_var = tk.StringVar()
        self.proxy_password_var = tk.StringVar()
        self.settings_message_var = tk.StringVar(value="密码仅在当前会话使用")

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        header = ttk.Frame(self.root, padding=(20, 14, 20, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="SpeedTest", font=("Segoe UI", 19, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text=f"v{__version__}", style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.test_page = ttk.Frame(self.notebook, padding=(20, 14, 20, 16))
        self.settings_page = ttk.Frame(self.notebook, padding=(20, 16, 20, 16))
        self.notebook.add(self.test_page, text="测速")
        self.notebook.add(self.settings_page, text="设置")
        self._build_test_page()
        self._build_settings_page()

    def _build_test_page(self) -> None:
        page = self.test_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(4, weight=1)
        top = ttk.Frame(page)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        self.state_label = ttk.Label(top, textvariable=self.state_var)
        self.state_label.grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.route_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e")

        speed = ttk.Frame(page)
        speed.grid(row=1, column=0, sticky="ew", pady=(8, 18))
        speed.columnconfigure(2, weight=1)
        ttk.Label(speed, textvariable=self.speed_var, font=("Segoe UI", 36, "bold"), foreground=BLUE).grid(row=0, column=0, sticky="sw")
        ttk.Label(speed, text="Mbps", font=("Segoe UI", 13), foreground=MUTED).grid(row=0, column=1, sticky="sw", padx=(9, 0), pady=(0, 10))
        ttk.Label(speed, textvariable=self.byte_speed_var, font=("Segoe UI", 13), foreground=MUTED).grid(row=0, column=2, sticky="se", pady=(0, 10))

        metrics = ttk.Frame(page)
        metrics.grid(row=2, column=0, sticky="ew", pady=(0, 17))
        for column, (label, variable) in enumerate((("平均速度", self.average_var), ("峰值速度", self.peak_var), ("累计流量", self.total_var), ("运行时间", self.elapsed_var))):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            group = ttk.Frame(metrics)
            group.grid(row=0, column=column, sticky="w")
            ttk.Label(group, text=label, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(group, textvariable=variable, font=("Segoe UI", 12, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 0))

        ttk.Separator(page).grid(row=3, column=0, sticky="ew")
        self.chart = tk.Canvas(page, height=round(154 * self.scale), background="white", highlightthickness=0)
        self.chart.grid(row=4, column=0, sticky="nsew", pady=(12, 6))
        self.chart.bind("<Configure>", lambda _event: self._draw_chart(self._last_snapshot))
        ttk.Label(page, textvariable=self.detail_var, style="Muted.TLabel", anchor="w").grid(row=5, column=0, sticky="ew", pady=(0, 12))
        actions = ttk.Frame(page)
        actions.grid(row=6, column=0, sticky="ew")
        actions.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(actions, text="开始测速", style="Primary.TButton", width=10, command=self.start)
        self.start_button.grid(row=0, column=0, padx=(0, 10))
        self.stop_button = ttk.Button(actions, text="停止", style="App.TButton", width=10, command=self.stop)
        self.stop_button.grid(row=0, column=1)
        ttk.Label(actions, textvariable=self.duration_label_var, style="Muted.TLabel").grid(row=0, column=2, sticky="e")

    def _build_settings_page(self) -> None:
        page = self.settings_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(5, weight=1)
        collection = ttk.Frame(page)
        collection.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        collection.columnconfigure(1, weight=1)
        ttk.Label(collection, text="测速集合").grid(row=0, column=0, padx=(0, 10))
        self.collection_entry = ttk.Combobox(
            collection,
            values=tuple(item.label for item in COLLECTIONS),
            textvariable=self.collection_var,
            state="readonly",
            width=22,
        )
        self.collection_entry.grid(row=0, column=1, sticky="w")
        self.collection_entry.bind("<<ComboboxSelected>>", lambda _event: self.select_collection())
        self.restore_collection_button = ttk.Button(
            collection,
            text="恢复集合默认",
            style="App.TButton",
            command=self.restore_collection,
        )
        self.restore_collection_button.grid(row=0, column=2, padx=(12, 0))

        timing = ttk.Frame(page)
        timing.grid(row=1, column=0, sticky="ew", pady=(0, 15))
        ttk.Label(timing, text="并发连接").grid(row=0, column=0, padx=(0, 10))
        self.connections_entry = ttk.Spinbox(timing, from_=1, to=64, textvariable=self.connections_var, width=5)
        self.connections_entry.grid(row=0, column=1)
        self.timed_check = ttk.Checkbutton(timing, text="定时停止", variable=self.timed_var, command=self._update_controls)
        self.timed_check.grid(row=0, column=2, padx=(28, 10))
        self.duration_entry = ttk.Spinbox(timing, from_=1, to=86400, textvariable=self.duration_var, width=7)
        self.duration_entry.grid(row=0, column=3)
        ttk.Label(timing, text="秒", style="Muted.TLabel").grid(row=0, column=4, padx=(7, 0))

        proxy = ttk.Frame(page)
        proxy.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        proxy.columnconfigure(3, weight=1)
        ttk.Label(proxy, text="代理类型").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.proxy_mode_entry = ttk.Combobox(proxy, values=tuple(PROXY_MODES), textvariable=self.proxy_mode_var, state="readonly", width=9)
        self.proxy_mode_entry.grid(row=0, column=1, sticky="w")
        self.proxy_mode_entry.bind("<<ComboboxSelected>>", lambda _event: self._update_controls())
        ttk.Label(proxy, text="地址").grid(row=0, column=2, padx=(20, 10))
        self.proxy_host_entry = ttk.Entry(proxy, textvariable=self.proxy_host_var, width=20)
        self.proxy_host_entry.grid(row=0, column=3, sticky="ew")
        ttk.Label(proxy, text="端口").grid(row=0, column=4, padx=(12, 8))
        self.proxy_port_entry = ttk.Entry(proxy, textvariable=self.proxy_port_var, width=7)
        self.proxy_port_entry.grid(row=0, column=5, sticky="e")

        auth = ttk.Frame(page)
        auth.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        auth.columnconfigure(1, weight=1)
        auth.columnconfigure(3, weight=1)
        ttk.Label(auth, text="用户名").grid(row=0, column=0, padx=(0, 10))
        self.proxy_username_entry = ttk.Entry(auth, textvariable=self.proxy_username_var)
        self.proxy_username_entry.grid(row=0, column=1, sticky="ew")
        ttk.Label(auth, text="密码").grid(row=0, column=2, padx=(20, 10))
        self.proxy_password_entry = ttk.Entry(auth, textvariable=self.proxy_password_var, show="●")
        self.proxy_password_entry.grid(row=0, column=3, sticky="ew")

        urls_header = ttk.Frame(page)
        urls_header.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        urls_header.columnconfigure(0, weight=1)
        ttk.Label(urls_header, text="下载地址", anchor="w").grid(row=0, column=0, sticky="w")
        urls_frame = ttk.Frame(page)
        urls_frame.grid(row=5, column=0, sticky="nsew")
        urls_frame.columnconfigure(0, weight=1)
        urls_frame.rowconfigure(0, weight=1)
        self.urls_text = tk.Text(urls_frame, height=7, width=1, wrap="none", font=("Consolas", 10), relief="flat", borderwidth=0, highlightthickness=1, highlightbackground="#cfd7e2", highlightcolor=BLUE, padx=8, pady=7, undo=True)
        self.urls_text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(urls_frame, orient="vertical", command=self.urls_text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(urls_frame, orient="horizontal", command=self.urls_text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.urls_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        footer = ttk.Frame(page)
        footer.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.settings_message_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.save_button = ttk.Button(footer, text="保存设置", width=10, style="Primary.TButton", command=self.save_settings)
        self.save_button.grid(row=0, column=1, sticky="e")

    def _fill_settings(self) -> None:
        self.collection_var.set(get_collection(self.config.collection_id).label)
        self.connections_var.set(str(self.config.connections))
        self.timed_var.set(self.config.duration_seconds > 0)
        self.duration_var.set(str(self.config.duration_seconds or 60))
        self.proxy_mode_var.set(next(label for label, mode in PROXY_MODES.items() if mode == self.config.proxy.mode))
        self.proxy_host_var.set(self.config.proxy.host)
        self.proxy_port_var.set(str(self.config.proxy.port))
        self.proxy_username_var.set(self.config.proxy.username)
        self.proxy_password_var.set(self.config.proxy.password)
        self.urls_text.insert("1.0", "\n".join(self.config.urls))
        self.route_var.set(self.config.proxy.label)
        self._update_controls()

    def _selected_collection(self):
        try:
            return COLLECTIONS_BY_LABEL[self.collection_var.get()]
        except KeyError as exc:
            raise ConfigError("请选择有效的测速集合。") from exc

    def select_collection(self) -> None:
        if self.engine.snapshot().busy:
            return
        collection = self._selected_collection()
        if collection.id == CUSTOM_COLLECTION_ID:
            self.settings_message_var.set("当前为自定义地址")
            self._update_controls()
            return
        self.urls_text.configure(state="normal")
        self.urls_text.delete("1.0", "end")
        self.urls_text.insert("1.0", "\n".join(collection.urls))
        self.settings_message_var.set(f"已载入 {collection.label} 默认地址")
        self._update_controls()

    def restore_collection(self) -> None:
        if self.engine.snapshot().busy:
            return
        collection = self._selected_collection()
        if collection.id == CUSTOM_COLLECTION_ID:
            return
        self.urls_text.configure(state="normal")
        self.urls_text.delete("1.0", "end")
        self.urls_text.insert("1.0", "\n".join(collection.urls))
        self.settings_message_var.set("集合默认地址已恢复")
        self._update_controls()

    def _read_settings(self) -> SpeedTestConfig:
        try:
            connections = int(self.connections_var.get())
            duration = int(self.duration_var.get()) if self.timed_var.get() else 0
            port = int(self.proxy_port_var.get())
        except ValueError as exc:
            raise ConfigError("并发数、时长和端口请填写整数。") from exc
        if self.timed_var.get() and duration <= 0:
            raise ConfigError("定时停止的时长至少为 1 秒。")
        collection = self._selected_collection()
        config = SpeedTestConfig(
            urls=tuple(line.strip() for line in self.urls_text.get("1.0", "end").splitlines() if line.strip()),
            connections=connections,
            duration_seconds=duration,
            proxy=ProxyConfig(
                mode=PROXY_MODES.get(self.proxy_mode_var.get(), ""),
                host=self.proxy_host_var.get().strip(),
                port=port,
                username=self.proxy_username_var.get(),
                password=self.proxy_password_var.get(),
            ),
            collection_id=collection.id,
        )
        config.validate()
        return config

    def save_settings(self) -> bool:
        if self.engine.snapshot().busy:
            return False
        try:
            config = self._read_settings()
            save_config(config, self.config_path)
        except ConfigError as error:
            self.notebook.select(self.settings_page)
            messagebox.showerror("设置有误", str(error), parent=self.root)
            return False
        except OSError:
            messagebox.showerror("保存失败", "无法写入用户配置目录。", parent=self.root)
            return False
        self.config = config
        self.route_var.set(config.proxy.label)
        self.settings_message_var.set("已保存 · 密码仅在当前会话使用")
        self._warning = ""
        return True

    def start(self) -> None:
        if not self.save_settings():
            return
        try:
            self.engine.start(self.config)
        except RuntimeError as error:
            messagebox.showerror("无法开始", str(error), parent=self.root)
            return
        self.notebook.select(self.test_page)
        self._render(self.engine.snapshot())

    def stop(self) -> None:
        self.engine.stop()
        self._render(self.engine.snapshot())

    def close(self) -> None:
        self._closing = True
        self.engine.stop("closed")
        self._render(self.engine.snapshot())

    def _update_controls(self) -> None:
        snapshot = self.engine.snapshot()
        busy = snapshot.busy or self._closing
        for widget in (self.collection_entry, self.restore_collection_button, self.connections_entry, self.timed_check, self.save_button):
            widget.state(["disabled" if busy else "!disabled"])
        self.proxy_mode_entry.configure(state="disabled" if busy else "readonly")
        self.urls_text.configure(state="disabled" if busy else "normal")
        if not busy:
            collection = COLLECTIONS_BY_LABEL.get(self.collection_var.get())
            is_custom = collection is not None and collection.id == CUSTOM_COLLECTION_ID
            self.restore_collection_button.state(["disabled" if is_custom else "!disabled"])
        self.duration_entry.state(["!disabled" if self.timed_var.get() and not busy else "disabled"])
        proxy_enabled = self.proxy_mode_var.get() != "直连" and not busy
        for widget in (self.proxy_host_entry, self.proxy_port_entry, self.proxy_username_entry, self.proxy_password_entry):
            widget.state(["!disabled" if proxy_enabled else "disabled"])
        self.notebook.tab(self.settings_page, state="disabled" if busy else "normal")
        self.start_button.state(["disabled" if busy else "!disabled"])
        self.stop_button.state(["!disabled" if snapshot.state == TestState.RUNNING and not self._closing else "disabled"])
        self._last_busy = busy

    def _tick(self) -> None:
        snapshot = self.engine.snapshot()
        if self._closing and not snapshot.busy:
            self._after_id = None
            self.root.destroy()
            return
        self._render(snapshot)
        self._after_id = self.root.after(100, self._tick)

    def _render(self, snapshot: SpeedSnapshot) -> None:
        self._last_snapshot = snapshot
        self.speed_var.set(f"{snapshot.mbps:,.2f}")
        self.byte_speed_var.set(f"{snapshot.megabytes_per_second:,.2f} MB/s")
        self.average_var.set(f"{snapshot.average_bytes_per_second * 8 / 1_000_000:,.2f} Mbps")
        self.peak_var.set(f"{snapshot.peak_bytes_per_second * 8 / 1_000_000:,.2f} Mbps")
        self.total_var.set(format_bytes(snapshot.total_bytes))
        self.elapsed_var.set(format_duration(snapshot.elapsed_seconds))
        if self._closing:
            status = "正在退出…"
        elif snapshot.state == TestState.STOPPING:
            status = "正在停止…"
        elif snapshot.state == TestState.RUNNING:
            status = f"测速中 · {snapshot.active_connections}/{snapshot.connections} 连接" if snapshot.active_connections else ("连接失败，正在重试…" if snapshot.last_error else "正在连接…")
        elif snapshot.state == TestState.FINISHED:
            status = "定时结束" if snapshot.stop_reason == "timer" else ("测速异常结束" if snapshot.stop_reason == "error" else "已停止")
        else:
            status = "就绪"
        self.state_var.set(status)
        self.state_label.configure(foreground=BLUE if snapshot.state == TestState.RUNNING else MUTED)
        self.detail_var.set(snapshot.last_error if snapshot.error_count else self._warning)
        if self.config.duration_seconds:
            remaining = max(0, math.ceil(self.config.duration_seconds - snapshot.elapsed_seconds))
            self.duration_label_var.set(f"剩余 {format_duration(remaining)}" if snapshot.busy else f"定时 {self.config.duration_seconds} 秒")
        else:
            self.duration_label_var.set("持续测速")
        if self._last_busy != (snapshot.busy or self._closing):
            self._update_controls()
        # Stop is disabled as soon as cancellation is requested, even while busy.
        self.stop_button.state(["!disabled" if snapshot.state == TestState.RUNNING and not self._closing else "disabled"])
        if self._last_chart_samples != snapshot.samples:
            self._draw_chart(snapshot)
            self._last_chart_samples = snapshot.samples

    def _draw_chart(self, snapshot: SpeedSnapshot) -> None:
        canvas = self.chart
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 80 or height < 50:
            return
        canvas.delete("all")
        left, top, right, bottom = 52, 16, width - 10, height - 24
        values = [sample.bytes_per_second * 8 / 1_000_000 for sample in snapshot.samples]
        maximum = max(values, default=0)
        if maximum <= 0:
            ceiling = 10.0
        else:
            step = 10 ** math.floor(math.log10(maximum))
            ceiling = max(step, math.ceil(maximum * 1.12 / step) * step)
        for fraction in (0, 0.5, 1):
            y = bottom - (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill=BORDER, dash=(3, 5))
            canvas.create_text(left - 9, y, text=f"{ceiling * fraction:g}", fill=MUTED, font=("Segoe UI", 9), anchor="e")
        canvas.create_text(left, bottom + 16, text="60 秒前", fill=MUTED, font=("Microsoft YaHei UI", 9), anchor="w")
        canvas.create_text(right, bottom + 16, text="现在", fill=MUTED, font=("Microsoft YaHei UI", 9), anchor="e")
        canvas.create_text(left, 0, text="Mbps", fill=MUTED, font=("Segoe UI", 8), anchor="nw")
        if snapshot.samples:
            end = snapshot.samples[-1].elapsed_seconds
            points = []
            for sample, value in zip(snapshot.samples, values):
                x = right - min(60, max(0, end - sample.elapsed_seconds)) / 60 * (right - left)
                y = bottom - value / ceiling * (bottom - top)
                points.extend((x, y))
            if len(points) >= 4:
                canvas.create_polygon(points[0], bottom, *points, points[-2], bottom, fill="#edf3ff", outline="")
                canvas.create_line(*points, fill=BLUE, width=2, capstyle="round", joinstyle="round")
            elif points:
                canvas.create_line(points[0] - 2, points[1], points[0], points[1], fill=BLUE, width=2)


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SpeedTest 下载测速")
    parser.add_argument("--smoke-test", type=Path, metavar="REPORT", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    report_path = arguments.smoke_test
    root = None
    result = {"ok": False, "version": __version__}
    try:
        _enable_dpi_awareness()
        root = tk.Tk()
        if report_path:
            root.withdraw()
        app = SpeedTestApp(root, config=SpeedTestConfig() if report_path else None)

        if report_path:
            def finish_smoke() -> None:
                try:
                    root.update_idletasks()
                    result.update({
                        "ok": app.start_button.instate(["!disabled"]) and app.stop_button.instate(["disabled"]),
                        "tabs": [app.notebook.tab(tab, "text") for tab in app.notebook.tabs()],
                        "python": sys.version.split()[0],
                        "tk": str(tk.TkVersion),
                        "frozen": bool(getattr(sys, "frozen", False)),
                    })
                finally:
                    app.close()

            def callback_error(error_type, _value, _traceback) -> None:
                result.update(ok=False, error=error_type.__name__)
                app.close()

            root.report_callback_exception = callback_error
            root.after(200, finish_smoke)
        root.mainloop()
    except Exception as error:
        result.update(ok=False, error=type(error).__name__)
        if not report_path:
            messagebox.showerror("SpeedTest", "程序启动失败，请重新下载完整版本。", parent=root)
        return 1
    finally:
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not report_path or result["ok"] else 1
