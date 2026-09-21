"""Compact two-page Tk interface. All widget access stays on the main thread."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import sys
import tkinter as tk
from dataclasses import replace
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
from .engine import SpeedSnapshot, SpeedTestEngine, TestState, ThreadSnapshot, ThreadState


PROXY_MODES = {"直连": "direct", "HTTP": "http", "HTTPS": "https", "SOCKS5": "socks5"}
BLUE = "#2563eb"
INK = "#182537"
MUTED = "#6b7789"
BORDER = "#e2e7ee"
NOTEBOOK_TAB_WIDTH = 8
NOTEBOOK_TAB_PADDING = (18, 8)
THREAD_STATE_LABELS = {
    ThreadState.IDLE: "未开始",
    ThreadState.CONNECTING: "连接中",
    ThreadState.DOWNLOADING: "测速中",
    ThreadState.RETRYING: "重试中",
    ThreadState.STOPPING: "停止中",
    ThreadState.STOPPED: "已停止",
}
THREAD_STATE_ORDER = {
    ThreadState.IDLE: 0,
    ThreadState.CONNECTING: 1,
    ThreadState.DOWNLOADING: 2,
    ThreadState.RETRYING: 3,
    ThreadState.STOPPING: 4,
    ThreadState.STOPPED: 5,
}
THREAD_DETAIL_COLUMNS = ("thread", "state", "speed", "total", "url")
THREAD_DETAIL_HEADINGS = {
    "thread": "线程",
    "state": "状态",
    "speed": "速度",
    "total": "累计流量",
    "url": "地址",
}
THREAD_DETAIL_FIXED_WIDTHS = {"thread": 64, "state": 76, "speed": 104, "total": 100}


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
        self._last_thread_rows = None
        self._current_thread_details: tuple[ThreadSnapshot, ...] = ()
        self._thread_row_frames: list[ttk.Frame] = []
        self._thread_address_labels: list[ttk.Label] = []
        self._thread_rows_by_index: dict[int, tuple[ttk.Frame, tuple[ttk.Label, ...]]] = {}
        self._thread_address_wraplength: int | None = None
        self._thread_scrollregion = None
        self._thread_sort_column: str | None = None
        self._thread_sort_reverse = False
        self._thread_display_order: tuple[int, ...] | None = None
        self._suppress_url_tracking = False
        self._urls_modified = False
        self.thread_details_expanded = False
        self._thread_details_count = 0
        self._thread_details_warning = ""
        self._active_collection_id = self.config.collection_id
        self._collection_urls: dict[str, tuple[str, ...]] = {self.config.collection_id: self.config.urls}
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)

        root.title("SpeedTest")
        root.geometry(f"{round(1000 * self.scale)}x{round(680 * self.scale)}")
        root.minsize(round(900 * self.scale), round(620 * self.scale))
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
        style.configure("ThreadHeader.TLabel", background="#f7f8fa", foreground=INK, padding=(6, 4))
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
        page.rowconfigure(5, weight=1)
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

        self.thread_details_section = ttk.Frame(page)
        self.thread_details_section.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.thread_details_section.columnconfigure(0, weight=1)
        self.thread_details_toggle = ttk.Button(
            self.thread_details_section,
            text="线程明细（0） ▶",
            style="App.TButton",
            command=self.toggle_thread_details,
        )
        self.thread_details_toggle.grid(row=0, column=0, sticky="w")

        self.thread_details_body = ttk.Frame(self.thread_details_section)
        self.thread_details_body.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.thread_details_body.columnconfigure(0, weight=1)
        self.thread_details_body.rowconfigure(1, weight=1)
        self.thread_details_header = ttk.Frame(self.thread_details_body)
        self.thread_details_header.grid(row=0, column=0, sticky="ew")
        self.thread_details_header_labels: dict[str, ttk.Label] = {}
        self._configure_thread_detail_grid(self.thread_details_header)
        for column in THREAD_DETAIL_COLUMNS:
            label = ttk.Label(
                self.thread_details_header,
                text=THREAD_DETAIL_HEADINGS[column],
                style="ThreadHeader.TLabel",
                anchor="e" if column in ("speed", "total") else "w",
                cursor="hand2",
            )
            label.grid(row=0, column=THREAD_DETAIL_COLUMNS.index(column), sticky="ew")
            label.bind("<Button-1>", lambda _event, sort_column=column: self.sort_thread_details(sort_column))
            self.thread_details_header_labels[column] = label

        self.thread_details_canvas = tk.Canvas(
            self.thread_details_body,
            height=round(104 * self.scale),
            background="white",
            highlightthickness=1,
            highlightbackground="#cfd7e2",
            highlightcolor=BLUE,
        )
        self.thread_details_canvas.grid(row=1, column=0, sticky="ew")
        self.thread_details_rows = ttk.Frame(self.thread_details_canvas)
        self.thread_details_rows.columnconfigure(0, weight=1)
        self._thread_details_window = self.thread_details_canvas.create_window(
            (0, 0), window=self.thread_details_rows, anchor="nw"
        )
        self.thread_details_rows.bind("<Configure>", self._on_thread_details_rows_configure)
        self.thread_details_canvas.bind("<Configure>", self._on_thread_details_canvas_configure)
        self.thread_details_scrollbar = ttk.Scrollbar(
            self.thread_details_body,
            orient="vertical",
            command=self.thread_details_canvas.yview,
        )
        self.thread_details_scrollbar.grid(row=1, column=1, sticky="ns")
        self.thread_details_canvas.configure(yscrollcommand=self.thread_details_scrollbar.set)
        self._update_thread_header_labels()
        self.thread_details_body.grid_remove()

        ttk.Separator(page).grid(row=4, column=0, sticky="ew")
        self.chart = tk.Canvas(page, height=round(154 * self.scale), background="white", highlightthickness=0)
        self.chart.grid(row=5, column=0, sticky="nsew", pady=(12, 6))
        self.chart.bind("<Configure>", lambda _event: self._draw_chart(self._last_snapshot))
        ttk.Label(page, textvariable=self.detail_var, style="Muted.TLabel", anchor="w").grid(row=6, column=0, sticky="ew", pady=(0, 12))
        actions = ttk.Frame(page)
        actions.grid(row=7, column=0, sticky="ew")
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
        ttk.Label(collection, text="测速集合").grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.collection_buttons: dict[str, ttk.Button] = {}
        for index, item in enumerate(COLLECTIONS, start=1):
            button = ttk.Button(
                collection,
                text=item.label,
                style="App.TButton",
                command=lambda collection_id=item.id: self.select_collection(collection_id),
            )
            button.grid(row=0, column=index, sticky="w", padx=(0, 8) if index < len(COLLECTIONS) else 0)
            self.collection_buttons[item.id] = button

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

        self.urls_header = ttk.Frame(page)
        self.urls_header.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.urls_header.columnconfigure(0, weight=1)
        ttk.Label(self.urls_header, text="下载地址", anchor="w").grid(row=0, column=0, sticky="w")
        self.restore_collection_button = ttk.Button(
            self.urls_header,
            text="恢复集合默认",
            style="App.TButton",
            command=self.restore_collection,
        )
        self.restore_collection_button.grid(row=0, column=1, sticky="e")
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
        self.urls_text.bind("<<Modified>>", self._on_urls_modified)

        footer = ttk.Frame(page)
        footer.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.settings_message_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.save_button = ttk.Button(footer, text="保存设置", width=10, style="Primary.TButton", command=self.save_settings)
        self.save_button.grid(row=0, column=1, sticky="e")

    def _fill_settings(self) -> None:
        collection = get_collection(self.config.collection_id)
        self._active_collection_id = collection.id
        self._collection_urls[collection.id] = self.config.urls
        self.thread_details_expanded = self.config.thread_details_expanded
        self._apply_thread_details_visibility()
        self.collection_var.set(collection.label)
        self.connections_var.set(str(self.config.connections))
        self.timed_var.set(self.config.duration_seconds > 0)
        self.duration_var.set(str(self.config.duration_seconds or 60))
        self.proxy_mode_var.set(next(label for label, mode in PROXY_MODES.items() if mode == self.config.proxy.mode))
        self.proxy_host_var.set(self.config.proxy.host)
        self.proxy_port_var.set(str(self.config.proxy.port))
        self.proxy_username_var.set(self.config.proxy.username)
        self.proxy_password_var.set(self.config.proxy.password)
        self._replace_urls(self.config.urls)
        self.route_var.set(self.config.proxy.label)
        self._update_urls_modified_state(show_message=True)
        self._update_controls()

    def _apply_thread_details_visibility(self) -> None:
        arrow = "▼" if self.thread_details_expanded else "▶"
        self.thread_details_toggle.configure(text=f"线程明细（{self._thread_details_count}） {arrow}")
        if self.thread_details_expanded:
            self.thread_details_body.grid()
        else:
            self.thread_details_body.grid_remove()

    def toggle_thread_details(self) -> None:
        self.thread_details_expanded = not self.thread_details_expanded
        self._apply_thread_details_visibility()
        self.config = replace(self.config, thread_details_expanded=self.thread_details_expanded)
        try:
            save_config(self.config, self.config_path)
            self._thread_details_warning = ""
        except (ConfigError, OSError):
            self._thread_details_warning = "线程明细展开状态保存失败"

    @staticmethod
    def _configure_thread_detail_grid(container: ttk.Frame) -> None:
        for index, column in enumerate(THREAD_DETAIL_COLUMNS):
            if column == "url":
                container.columnconfigure(index, weight=1, minsize=1)
            else:
                container.columnconfigure(index, weight=0, minsize=THREAD_DETAIL_FIXED_WIDTHS[column])

    def _on_thread_details_rows_configure(self, _event: tk.Event) -> None:
        self._update_thread_details_scrollregion()

    def _update_thread_details_scrollregion(self) -> None:
        scrollregion = self.thread_details_canvas.bbox("all")
        if scrollregion != self._thread_scrollregion:
            self.thread_details_canvas.configure(scrollregion=scrollregion)
            self._thread_scrollregion = scrollregion

    def _on_thread_details_canvas_configure(self, event: tk.Event) -> None:
        self.thread_details_canvas.itemconfigure(self._thread_details_window, width=max(1, event.width))
        self._update_thread_address_wraplength()

    def _update_thread_address_wraplength(self, *, force: bool = False) -> None:
        width = self.thread_details_canvas.winfo_width()
        if width <= 1:
            return
        fixed_width = sum(THREAD_DETAIL_FIXED_WIDTHS.values())
        address_width = max(160, width - fixed_width - 8)
        if not force and address_width == self._thread_address_wraplength:
            return
        self._thread_address_wraplength = address_width
        for label in self._thread_address_labels:
            label.configure(wraplength=address_width)
        self._update_thread_details_scrollregion()

    def _update_thread_header_labels(self) -> None:
        for column, label in self.thread_details_header_labels.items():
            heading = THREAD_DETAIL_HEADINGS[column]
            if column == self._thread_sort_column:
                heading = f"{heading} {'▼' if self._thread_sort_reverse else '▲'}"
            label.configure(text=heading)

    def _thread_detail_sort_key(self, detail: ThreadSnapshot):
        column = self._thread_sort_column
        if column == "state":
            return THREAD_STATE_ORDER[detail.state]
        if column == "speed":
            return detail.bytes_per_second
        if column == "total":
            return detail.total_bytes
        if column == "url":
            return detail.url.casefold()
        return detail.index

    def sort_thread_details(self, column: str) -> None:
        if column not in THREAD_DETAIL_COLUMNS:
            return
        if column == self._thread_sort_column:
            self._thread_sort_reverse = not self._thread_sort_reverse
        else:
            self._thread_sort_column = column
            self._thread_sort_reverse = False
        ordered_details = tuple(
            sorted(
                self._current_thread_details,
                key=self._thread_detail_sort_key,
                reverse=self._thread_sort_reverse,
            )
        )
        self._thread_display_order = tuple(detail.index for detail in ordered_details)
        self._update_thread_header_labels()
        self._last_thread_rows = None
        self._update_thread_details(self._current_thread_details)

    def _ordered_thread_details(self, details: tuple[ThreadSnapshot, ...]) -> tuple[ThreadSnapshot, ...]:
        if self._thread_display_order is None:
            return tuple(sorted(details, key=lambda detail: detail.index))

        details_by_index = {detail.index: detail for detail in details}
        ordered = [
            details_by_index[index]
            for index in self._thread_display_order
            if index in details_by_index
        ]
        displayed_indexes = {detail.index for detail in ordered}
        ordered.extend(
            sorted(
                (detail for detail in details if detail.index not in displayed_indexes),
                key=lambda detail: detail.index,
            )
        )
        return tuple(ordered)

    def _preview_thread_details(self) -> tuple[ThreadSnapshot, ...]:
        return tuple(
            ThreadSnapshot(
                index=index + 1,
                url=self.config.urls[index % len(self.config.urls)],
                state=ThreadState.IDLE,
                total_bytes=0,
                bytes_per_second=0.0,
            )
            for index in range(self.config.connections)
        )

    def _update_thread_details(self, details: tuple[ThreadSnapshot, ...]) -> None:
        self._current_thread_details = tuple(details)
        ordered_details = self._ordered_thread_details(self._current_thread_details)
        rows = tuple(
            (
                f"线程 {detail.index}",
                THREAD_STATE_LABELS[detail.state],
                f"{detail.bytes_per_second * 8 / 1_000_000:,.2f} Mbps",
                format_bytes(detail.total_bytes),
                detail.url,
            )
            for detail in ordered_details
        )
        if rows == self._last_thread_rows:
            return
        self._last_thread_rows = rows
        count_changed = self._thread_details_count != len(rows)
        self._thread_details_count = len(rows)
        if count_changed:
            self._apply_thread_details_visibility()

        active_indexes = {detail.index for detail in ordered_details}
        structure_changed = False
        for index in tuple(self._thread_rows_by_index):
            if index not in active_indexes:
                self._thread_rows_by_index[index][0].destroy()
                del self._thread_rows_by_index[index]
                structure_changed = True

        self._thread_row_frames = []
        self._thread_address_labels = []
        for row_index, (detail, row) in enumerate(zip(ordered_details, rows)):
            row_widgets = self._thread_rows_by_index.get(detail.index)
            if row_widgets is None:
                row_frame = ttk.Frame(self.thread_details_rows)
                self._configure_thread_detail_grid(row_frame)
                labels = []
                for column_index, value in enumerate(row):
                    label = ttk.Label(
                        row_frame,
                        text=value,
                        anchor="e" if column_index in (2, 3) else "w",
                        justify="left",
                        padding=(6, 3),
                    )
                    label.grid(row=0, column=column_index, sticky="ew")
                    labels.append(label)
                row_widgets = (row_frame, tuple(labels))
                self._thread_rows_by_index[detail.index] = row_widgets
                structure_changed = True

            row_frame, labels = row_widgets
            row_frame.grid(row=row_index, column=0, sticky="ew")
            for label, value in zip(labels, row):
                if label.cget("text") != value:
                    label.configure(text=value)
            self._thread_row_frames.append(row_frame)
            self._thread_address_labels.append(labels[4])
        if structure_changed:
            self._update_thread_address_wraplength(force=True)
            self._update_thread_details_scrollregion()

    def _selected_collection(self):
        try:
            return COLLECTIONS_BY_LABEL[self.collection_var.get()]
        except KeyError as exc:
            raise ConfigError("请选择有效的测速集合。") from exc

    def select_collection(self, collection_id: str | None = None) -> None:
        if self.engine.snapshot().busy:
            return
        if collection_id is not None:
            self.collection_var.set(get_collection(collection_id).label)
        collection = self._selected_collection()
        self._remember_active_collection_urls()
        if collection.id == CUSTOM_COLLECTION_ID:
            if collection.id not in self._collection_urls:
                self._collection_urls[collection.id] = self._read_urls()
            self._active_collection_id = collection.id
            self._replace_urls(self._collection_urls[collection.id])
            self.settings_message_var.set("当前为自定义地址")
            self._update_controls()
            return
        urls = self._collection_urls.get(collection.id, collection.urls)
        self._active_collection_id = collection.id
        self._replace_urls(urls)
        self._update_urls_modified_state()
        source = "自定义地址" if self._urls_modified else "默认地址"
        self.settings_message_var.set(f"已载入 {collection.label} {source}")
        self._update_controls()

    def restore_collection(self) -> None:
        if self.engine.snapshot().busy:
            return
        collection = self._selected_collection()
        if collection.id == CUSTOM_COLLECTION_ID:
            return
        self._collection_urls[collection.id] = collection.urls
        self._active_collection_id = collection.id
        self._replace_urls(collection.urls)
        self.settings_message_var.set("集合默认地址已恢复")
        self._update_controls()

    def _replace_urls(self, urls: tuple[str, ...]) -> None:
        self._suppress_url_tracking = True
        try:
            self.urls_text.configure(state="normal")
            self.urls_text.delete("1.0", "end")
            self.urls_text.insert("1.0", "\n".join(urls))
            self.urls_text.edit_modified(False)
        finally:
            self._suppress_url_tracking = False
        self._urls_modified = False

    def _read_urls(self) -> tuple[str, ...]:
        return tuple(line.strip() for line in self.urls_text.get("1.0", "end").splitlines() if line.strip())

    def _remember_active_collection_urls(self) -> None:
        self._collection_urls[self._active_collection_id] = self._read_urls()

    def _update_urls_modified_state(self, *, show_message: bool = False) -> None:
        collection = self._selected_collection()
        self._urls_modified = collection.id == CUSTOM_COLLECTION_ID or self._read_urls() != collection.urls
        if show_message and self._urls_modified:
            message = "当前为自定义地址" if collection.id == CUSTOM_COLLECTION_ID else f"已编辑 {collection.label} 地址"
            self.settings_message_var.set(message)

    def _on_urls_modified(self, _event: tk.Event) -> None:
        if not self.urls_text.edit_modified():
            return
        self.urls_text.edit_modified(False)
        if self._suppress_url_tracking or self.engine.snapshot().busy:
            return
        self._remember_active_collection_urls()
        self._update_urls_modified_state(show_message=True)

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
            urls=self._read_urls(),
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
            thread_details_expanded=self.thread_details_expanded,
        )
        config.validate()
        return config

    @staticmethod
    def _matches_persisted_config(current: SpeedTestConfig, persisted: SpeedTestConfig) -> bool:
        return (
            current.collection_id == persisted.collection_id
            and current.urls == persisted.urls
            and current.connections == persisted.connections
            and current.duration_seconds == persisted.duration_seconds
            and current.proxy.mode == persisted.proxy.mode
            and current.proxy.host == persisted.proxy.host
            and current.proxy.port == persisted.proxy.port
            and current.proxy.username == persisted.proxy.username
            and current.thread_details_expanded == persisted.thread_details_expanded
        )

    def save_settings(self) -> bool:
        if self.engine.snapshot().busy:
            return False
        try:
            config = self._read_settings()
            save_config(config, self.config_path)
            persisted, warning = load_config(self.config_path)
        except ConfigError as error:
            self.notebook.select(self.settings_page)
            messagebox.showerror("设置有误", str(error), parent=self.root)
            return False
        except OSError:
            messagebox.showerror("保存失败", "无法写入用户配置目录。", parent=self.root)
            return False
        if warning or not self._matches_persisted_config(config, persisted):
            messagebox.showerror("保存失败", "保存后校验失败，请重新保存。", parent=self.root)
            return False
        self.config = config
        self._collection_urls[config.collection_id] = config.urls
        self._active_collection_id = config.collection_id
        self.route_var.set(config.proxy.label)
        self._update_urls_modified_state()
        address_status = f"已保存 {self._selected_collection().label} 自定义地址" if self._urls_modified else "已保存"
        self.settings_message_var.set(f"{address_status} · 密码仅在当前会话使用")
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
        for button in self.collection_buttons.values():
            button.state(["disabled" if busy else "!disabled"])
        for widget in (self.restore_collection_button, self.connections_entry, self.timed_check, self.save_button):
            widget.state(["disabled" if busy else "!disabled"])
        collection = COLLECTIONS_BY_LABEL.get(self.collection_var.get())
        selected_id = collection.id if collection is not None else None
        for collection_id, button in self.collection_buttons.items():
            button.configure(style="Primary.TButton" if collection_id == selected_id else "App.TButton")
        self.proxy_mode_entry.configure(state="disabled" if busy else "readonly")
        self.urls_text.configure(state="disabled" if busy else "normal")
        if not busy:
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
        if snapshot.error_count:
            self.detail_var.set(snapshot.last_error)
        elif self._thread_details_warning:
            self.detail_var.set(self._thread_details_warning)
        else:
            self.detail_var.set(self._warning)
        self._update_thread_details(snapshot.thread_details or self._preview_thread_details())
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
