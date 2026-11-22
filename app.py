from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from engine import (
    CHANGE_NEW,
    CHANGE_SAME,
    CHANGE_UPDATE,
    HARDWARE_CLASSES,
    apply_override,
    batch_name,
    build_index,
    detect_xml_kind,
    export_matches,
    full_report_csv,
    load_document,
    match_diff,
    match_svt,
    missing_matches,
    missing_report,
    nearest_version,
    resolve_swe_root,
    summarize,
    zip_folder,
)
from i18n import LANG_NAMES, LANGS, NAME_TO_LANG, normalize_language, system_language, translate
from version import APP_VERSION

try:  # optional: drag & drop onto the window
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:  # pragma: no cover - depends on the install
    _BaseTk = tk.Tk
    DND_FILES = None
    HAS_DND = False

APP_TITLE = "SVT → PSdZData Export"
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "svt_export.json"
DEFAULT_PSDZ = Path(r"C:\PSdZData 4.60.11 Lite")
DEFAULT_SVT = DEFAULT_PSDZ / "SVT" / "svt for soft targ.xml"
DEFAULT_OUT = Path.home() / "Desktop" / "SVT_Export"

MODES = ("single", "diff", "tal", "batch")
LAYOUTS = ("psdzdata", "swe", "ecu", "flat")
LAYOUT_KEYS = {
    "psdzdata": "layout_psdzdata",
    "swe": "layout_swe",
    "ecu": "layout_ecu",
    "flat": "layout_flat",
}
COLUMNS = ("chk", "action", "cls", "ident", "ist", "ver", "change", "status", "files")
MODE_COLUMNS = {
    "single": ("chk", "cls", "ident", "ver", "status", "files"),
    "diff": ("chk", "cls", "ident", "ist", "ver", "change", "status", "files"),
    "tal": ("chk", "action", "cls", "ident", "ver", "status", "files"),
}
BATCH_COLUMNS = ("chk", "file", "parts", "found", "other", "missing", "state", "folder")

MARK_ON = "☑"
MARK_OFF = "☐"
MARK_MIX = "☒"


def load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(size, 1) / 1024:.1f} KB" if size else "0 KB"


class App(_BaseTk):
    def __init__(self, lang: str | None = None, use_config: bool = True) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x820")
        self.minsize(1000, 660)
        self.matches = []
        self.index = None
        self.doc = None
        self.doc_ist = None
        self._busy = False
        self.class_vars: dict[str, tk.BooleanVar] = {}
        self.checked: dict[str, bool] = {}
        self.part_by_iid: dict[str, object] = {}
        self.ecu_parts: dict[str, list[str]] = {}
        self.ecu_of: dict[str, object] = {}
        self.batch_files: list[str] = []
        self.batch_checked: dict[str, bool] = {}
        self.batch_results: dict[str, dict] = {}
        self.remember_paths = use_config
        self._on_search_done = None
        self._on_export_done = None
        cfg = load_config() if use_config else {}
        if lang in LANGS:
            self._lang = lang
        else:
            self._lang = normalize_language(cfg.get("language")) if cfg.get("language") in LANGS else system_language()

        self.mode_var = tk.StringVar(value=cfg.get("mode", "single") if cfg.get("mode") in MODES else "single")
        self.psdz_var = tk.StringVar(value=cfg.get("psdz", str(DEFAULT_PSDZ if DEFAULT_PSDZ.exists() else "")))
        self.svt_var = tk.StringVar(value=cfg.get("svt", str(DEFAULT_SVT if DEFAULT_SVT.exists() else "")))
        self.ist_var = tk.StringVar(value=cfg.get("svt_ist", ""))
        self.soll_var = tk.StringVar(value=cfg.get("svt_soll", ""))
        self.tal_var = tk.StringVar(value=cfg.get("tal", ""))
        self.out_var = tk.StringVar(value=cfg.get("out", str(DEFAULT_OUT)))
        self.layout_var = tk.StringVar(value=cfg.get("layout", "psdzdata"))
        self.lang_var = tk.StringVar(value=LANG_NAMES[self._lang])
        self.status_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value="")
        self.filter_var = tk.StringVar(value="")
        opts = cfg.get("options", {})
        self.only_changed_var = tk.BooleanVar(value=opts.get("only_changed", True))
        self.hide_same_var = tk.BooleanVar(value=opts.get("hide_same", True))
        self.nearest_var = tk.BooleanVar(value=opts.get("nearest", False))
        self.zip_var = tk.BooleanVar(value=opts.get("zip", False))
        self.hardlink_var = tk.BooleanVar(value=opts.get("hardlink", False))
        self.hash_var = tk.BooleanVar(value=opts.get("hash", False))
        self.batch_shared_var = tk.BooleanVar(value=opts.get("batch_shared", False))
        self.batch_files = [item for item in cfg.get("batch", []) if isinstance(item, str)]

        self._build()
        self._apply_language()
        self._apply_mode()
        self.filter_var.trace_add("write", lambda *_: self._render_tree())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def tr(self, key: str, **kwargs) -> str:
        return translate(self._lang, key, **kwargs)

    @property
    def mode(self) -> str:
        return self.mode_var.get()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")
        self.lbl_source = ttk.Label(top)
        self.lbl_source.pack(side="left")
        self.mode_buttons: dict[str, ttk.Radiobutton] = {}
        for value in MODES:
            button = ttk.Radiobutton(
                top, value=value, variable=self.mode_var, style="Toolbutton", command=self._on_mode
            )
            button.pack(side="left", padx=(6 if value == "single" else 0, 0), ipadx=6)
            self.mode_buttons[value] = button
        self.lang_combo = ttk.Combobox(
            top,
            textvariable=self.lang_var,
            values=list(LANG_NAMES.values()),
            state="readonly",
            width=14,
        )
        self.lang_combo.pack(side="right")
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language)
        self.lbl_lang = ttk.Label(top)
        self.lbl_lang.pack(side="right", padx=(0, 6))

        self.paths_frame = ttk.LabelFrame(root, text="", padding=8)
        self.paths_frame.pack(fill="x", pady=(8, 0))
        self.row_psdz = self._path_row(self.paths_frame, 0, self.psdz_var, self._browse_psdz)
        self.row_svt = self._path_row(self.paths_frame, 1, self.svt_var, lambda: self._browse_xml(self.svt_var))
        self.row_ist = self._path_row(self.paths_frame, 2, self.ist_var, lambda: self._browse_xml(self.ist_var))
        self.row_soll = self._path_row(self.paths_frame, 3, self.soll_var, lambda: self._browse_xml(self.soll_var))
        self.row_tal = self._path_row(self.paths_frame, 4, self.tal_var, lambda: self._browse_xml(self.tal_var))
        self.row_out = self._path_row(self.paths_frame, 5, self.out_var, self._browse_out)
        self.drop_hint = ttk.Label(self.paths_frame, foreground="#3d5a7a")
        self.drop_hint.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        opts = ttk.Frame(root)
        opts.pack(fill="x", pady=(8, 0))

        self.types_frame = ttk.LabelFrame(opts, text="", padding=8)
        self.types_frame.pack(side="left", fill="both", expand=True)

        self.options_frame = ttk.LabelFrame(opts, text="", padding=8)
        self.options_frame.pack(side="left", fill="y", padx=(8, 0))
        self.chk_only_changed = ttk.Checkbutton(
            self.options_frame, variable=self.only_changed_var, command=self._on_only_changed
        )
        self.chk_nearest = ttk.Checkbutton(self.options_frame, variable=self.nearest_var, command=self._on_nearest)
        self.chk_batch_shared = ttk.Checkbutton(self.options_frame, variable=self.batch_shared_var)
        self.chk_zip = ttk.Checkbutton(self.options_frame, variable=self.zip_var)
        self.chk_hardlink = ttk.Checkbutton(self.options_frame, variable=self.hardlink_var)
        self.chk_hash = ttk.Checkbutton(self.options_frame, variable=self.hash_var)
        for widget in (
            self.chk_only_changed,
            self.chk_nearest,
            self.chk_batch_shared,
            self.chk_zip,
            self.chk_hardlink,
            self.chk_hash,
        ):
            widget.pack(anchor="w")

        self.layout_frame = ttk.LabelFrame(opts, text="", padding=8)
        self.layout_frame.pack(side="left", fill="y", padx=(8, 0))
        self.layout_buttons: dict[str, ttk.Radiobutton] = {}
        for value in LAYOUTS:
            button = ttk.Radiobutton(self.layout_frame, text="", value=value, variable=self.layout_var)
            button.pack(anchor="w")
            self.layout_buttons[value] = button

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(8, 0))
        self.search_btn = ttk.Button(actions, command=self.start_search)
        self.search_btn.pack(side="left")
        self.export_btn = ttk.Button(actions, command=self.start_export, state="disabled")
        self.export_btn.pack(side="left", padx=(8, 0))
        self.open_btn = ttk.Button(actions, command=self._open_out)
        self.open_btn.pack(side="left", padx=(8, 0))
        ttk.Separator(actions, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)
        self.lbl_blocks = ttk.Label(actions)
        self.lbl_blocks.pack(side="left")
        self.btn_all = ttk.Button(actions, command=lambda: self._set_all_checked(True))
        self.btn_all.pack(side="left", padx=(6, 0))
        self.btn_none = ttk.Button(actions, command=lambda: self._set_all_checked(False))
        self.btn_none.pack(side="left", padx=(4, 0))
        self.btn_found = ttk.Button(actions, command=self._select_found)
        self.btn_found.pack(side="left", padx=(4, 0))
        ttk.Label(root, textvariable=self.summary_var, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))

        tools = ttk.Frame(root)
        tools.pack(fill="x", pady=(4, 6))
        self.tools_single = ttk.Frame(tools)
        self.btn_missing = ttk.Button(self.tools_single, command=self.show_missing_dialog)
        self.btn_missing.pack(side="left")
        self.btn_report = ttk.Button(self.tools_single, command=self.save_report_csv)
        self.btn_report.pack(side="left", padx=(4, 0))
        self.chk_hide_same = ttk.Checkbutton(
            self.tools_single, variable=self.hide_same_var, command=self._render_tree
        )
        self.chk_hide_same.pack(side="left", padx=(12, 0))
        self.tools_batch = ttk.Frame(tools)
        self.btn_batch_add = ttk.Button(self.tools_batch, command=self._batch_add_files)
        self.btn_batch_add.pack(side="left")
        self.btn_batch_folder = ttk.Button(self.tools_batch, command=self._batch_add_folder)
        self.btn_batch_folder.pack(side="left", padx=(4, 0))
        self.btn_batch_remove = ttk.Button(self.tools_batch, command=self._batch_remove)
        self.btn_batch_remove.pack(side="left", padx=(4, 0))
        filter_box = ttk.Frame(tools)
        filter_box.pack(side="right")
        self.lbl_filter = ttk.Label(filter_box)
        self.lbl_filter.pack(side="left", padx=(0, 6))
        ttk.Entry(filter_box, textvariable=self.filter_var, width=24).pack(side="left")

        panes = ttk.Panedwindow(root, orient="vertical")
        panes.pack(fill="both", expand=True)

        self.tree_wrap = ttk.Frame(panes)
        self.tree = ttk.Treeview(self.tree_wrap, columns=COLUMNS, show="tree headings", selectmode="extended")
        self.tree.heading("chk", text="✓", command=self._toggle_all_heading)
        self.tree.heading("ident", text="ID")
        self.tree.column("#0", width=280, stretch=True)
        self.tree.column("chk", width=36, anchor="center", stretch=False)
        self.tree.column("action", width=90, anchor="center")
        self.tree.column("cls", width=60, anchor="center")
        self.tree.column("ident", width=90, anchor="center")
        self.tree.column("ist", width=100, anchor="center")
        self.tree.column("ver", width=100, anchor="center")
        self.tree.column("change", width=90, anchor="center")
        self.tree.column("status", width=200)
        self.tree.column("files", width=300)
        yscroll = ttk.Scrollbar(self.tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        for widget in (self.tree,):
            widget.tag_configure("found", foreground="#0a7a32")
            widget.tag_configure("other", foreground="#b36b00")
            widget.tag_configure("override", foreground="#b36b00")
            widget.tag_configure("warn", foreground="#8a5a00")
            widget.tag_configure("missing", foreground="#b42318")
            widget.tag_configure("nofiles", foreground="#7a7a7a")
            widget.tag_configure("same", foreground="#7a7a7a")
            widget.tag_configure("ecu", font=("Segoe UI", 9, "bold"))
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<Button-3>", self._on_tree_menu)
        self.tree.bind("<Double-1>", self._on_tree_double)

        self.batch_wrap = ttk.Frame(panes)
        self.batch_tree = ttk.Treeview(self.batch_wrap, columns=BATCH_COLUMNS, show="headings", selectmode="extended")
        widths = {"chk": 36, "file": 300, "parts": 90, "found": 70, "other": 90, "missing": 70, "state": 220, "folder": 220}
        for col in BATCH_COLUMNS:
            self.batch_tree.column(col, width=widths[col], anchor="w" if col in ("file", "state", "folder") else "center")
        self.batch_tree.heading("chk", text="✓", command=self._batch_toggle_all)
        bscroll = ttk.Scrollbar(self.batch_wrap, orient="vertical", command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=bscroll.set)
        self.batch_tree.pack(side="left", fill="both", expand=True)
        bscroll.pack(side="right", fill="y")
        self.batch_tree.tag_configure("found", foreground="#0a7a32")
        self.batch_tree.tag_configure("other", foreground="#b36b00")
        self.batch_tree.tag_configure("missing", foreground="#b42318")
        self.batch_tree.bind("<Button-1>", self._on_batch_click)

        log_wrap = ttk.Frame(panes)
        self.log = tk.Text(log_wrap, height=6, wrap="word", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.panes = panes
        panes.add(self.tree_wrap, weight=4)
        panes.add(log_wrap, weight=1)
        self.log_wrap = log_wrap

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w", pady=(4, 0))

        self._set_class_checks(["BTLD", "SWFL", "SWFK", "CAFD", "HWEL", "HWAP"])
        if HAS_DND:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

    def _path_row(self, parent, row: int, variable: tk.StringVar, command):
        label = ttk.Label(parent, width=14)
        label.grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        button = ttk.Button(parent, command=command)
        button.grid(row=row, column=2, pady=2)
        parent.columnconfigure(1, weight=1)
        return label, entry, button

    def _show_row(self, row, visible: bool) -> None:
        for widget in row:
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def load_demo_paths(self, mode: str = "single") -> Path:
        demo = Path(__file__).resolve().parent / "demo"
        self.remember_paths = False
        self.psdz_var.set(str(demo))
        self.svt_var.set(str(demo / "sample-svt.xml"))
        self.ist_var.set(str(demo / "sample-svt-ist.xml"))
        self.soll_var.set(str(demo / "sample-svt.xml"))
        self.tal_var.set(str(demo / "sample-tal.xml"))
        self.out_var.set(str(demo / "out"))
        self.batch_files = [str(demo / "sample-svt.xml"), str(demo / "sample-svt-ist.xml")]
        self.mode_var.set(mode)
        self._apply_mode()
        return demo

    # ---------------------------------------------------------------- language
    def _on_language(self, _event=None) -> None:
        lang = NAME_TO_LANG.get(self.lang_var.get())
        if not lang or lang == self._lang:
            return
        self._lang = lang
        self._apply_language()
        self._persist()

    def set_language(self, lang: str) -> None:
        self._lang = lang
        self.lang_var.set(LANG_NAMES[lang])
        self._apply_language()

    def _apply_language(self) -> None:
        self.title(f"{self.tr('app_title')} {APP_VERSION}")
        self.lbl_source.configure(text=self.tr("source"))
        for value, button in self.mode_buttons.items():
            button.configure(text=self.tr(f"mode_{value}"))
        self.paths_frame.configure(text=self.tr("paths"))
        self.row_psdz[0].configure(text=self.tr("path_psdz"))
        self.row_svt[0].configure(text=self.tr("path_svt"))
        self.row_ist[0].configure(text=self.tr("path_ist"))
        self.row_soll[0].configure(text=self.tr("path_soll"))
        self.row_tal[0].configure(text=self.tr("path_tal"))
        self.row_out[0].configure(text=self.tr("path_out"))
        for row in (self.row_psdz, self.row_svt, self.row_ist, self.row_soll, self.row_tal, self.row_out):
            row[2].configure(text=self.tr("browse"))
        self.drop_hint.configure(text=self.tr("drop_hint") if HAS_DND else self.tr("drop_hint_exe"))
        self.lbl_lang.configure(text=self.tr("language"))
        self.types_frame.configure(text=self.tr("export_types"))
        self.options_frame.configure(text=self.tr("export_options"))
        self.chk_only_changed.configure(text=self.tr("opt_only_changed"))
        self.chk_nearest.configure(text=self.tr("opt_nearest"))
        self.chk_batch_shared.configure(text=self.tr("opt_batch_shared"))
        self.chk_zip.configure(text=self.tr("opt_zip"))
        self.chk_hardlink.configure(text=self.tr("opt_hardlink"))
        self.chk_hash.configure(text=self.tr("opt_hash"))
        self.layout_frame.configure(text=self.tr("export_layout"))
        for value, button in self.layout_buttons.items():
            button.configure(text=self.tr(LAYOUT_KEYS[value]))
        self.export_btn.configure(text=self.tr("export_selected"))
        self.open_btn.configure(text=self.tr("open_export"))
        self.lbl_blocks.configure(text=self.tr("blocks"))
        self.btn_all.configure(text=self.tr("select_all"))
        self.btn_none.configure(text=self.tr("select_none"))
        self.btn_found.configure(text=self.tr("select_found"))
        self.btn_missing.configure(text=self.tr("btn_missing"))
        self.btn_report.configure(text=self.tr("btn_report_csv"))
        self.chk_hide_same.configure(text=self.tr("opt_hide_same"))
        self.btn_batch_add.configure(text=self.tr("batch_add"))
        self.btn_batch_folder.configure(text=self.tr("batch_add_folder"))
        self.btn_batch_remove.configure(text=self.tr("batch_remove"))
        self.lbl_filter.configure(text=self.tr("filter"))
        self.tree.heading("#0", text=self.tr("col_ecu"))
        self.tree.heading("action", text=self.tr("col_action"))
        self.tree.heading("cls", text=self.tr("col_class"))
        self.tree.heading("ist", text=self.tr("col_ist"))
        self.tree.heading("ver", text=self.tr("col_soll") if self.mode == "diff" else self.tr("col_ver"))
        self.tree.heading("change", text=self.tr("col_change"))
        self.tree.heading("status", text=self.tr("col_status"))
        self.tree.heading("files", text=self.tr("col_files"))
        for col in BATCH_COLUMNS[1:]:
            self.batch_tree.heading(col, text=self.tr(f"bcol_{col}"))
        self._update_search_label()
        if self.matches:
            self._render_tree()
            self._refresh_summary()
        elif self.mode == "batch":
            self._render_batch()
        elif not self._busy:
            self.status_var.set(self.tr("status_ready"))

    def _update_search_label(self) -> None:
        key = {"diff": "search_diff", "tal": "search_tal", "batch": "search_batch"}.get(self.mode, "search")
        self.search_btn.configure(text=self.tr(key))

    # -------------------------------------------------------------------- mode
    def _on_mode(self) -> None:
        self._clear_results()
        self._apply_mode()
        self._apply_language()

    def _apply_mode(self) -> None:
        mode = self.mode
        self._show_row(self.row_svt, mode == "single")
        self._show_row(self.row_ist, mode == "diff")
        self._show_row(self.row_soll, mode == "diff")
        self._show_row(self.row_tal, mode == "tal")
        for widget, visible in (
            (self.chk_only_changed, mode == "diff"),
            (self.chk_nearest, True),
            (self.chk_batch_shared, mode == "batch"),
        ):
            if visible:
                widget.pack(anchor="w", before=self.chk_zip)
            else:
                widget.pack_forget()
        if mode == "batch":
            self.tools_single.pack_forget()
            self.tools_batch.pack(side="left")
            if str(self.tree_wrap) in self.panes.panes():
                self.panes.forget(self.tree_wrap)
            if str(self.batch_wrap) not in self.panes.panes():
                self.panes.insert(0, self.batch_wrap, weight=4)
            self._render_batch()
        else:
            self.tools_batch.pack_forget()
            self.tools_single.pack(side="left")
            if mode == "diff":
                self.chk_hide_same.pack(side="left", padx=(12, 0))
            else:
                self.chk_hide_same.pack_forget()
            if str(self.batch_wrap) in self.panes.panes():
                self.panes.forget(self.batch_wrap)
            if str(self.tree_wrap) not in self.panes.panes():
                self.panes.insert(0, self.tree_wrap, weight=4)
            self.tree.configure(displaycolumns=MODE_COLUMNS[mode])
        self._update_search_label()

    def _clear_results(self) -> None:
        self.matches = []
        self.doc = None
        self.doc_ist = None
        self.checked = {}
        self.part_by_iid = {}
        self.ecu_parts = {}
        self.tree.delete(*self.tree.get_children())
        self.summary_var.set("")
        self.export_btn.configure(state="disabled")

    # ----------------------------------------------------------- types/paths
    def _set_class_checks(self, classes: list[str]) -> None:
        previous = {name: var.get() for name, var in self.class_vars.items()}
        for child in self.types_frame.winfo_children():
            child.destroy()
        self.class_vars = {}
        for name in classes:
            default = previous.get(name, name not in HARDWARE_CLASSES)
            var = tk.BooleanVar(value=default)
            self.class_vars[name] = var
            ttk.Checkbutton(
                self.types_frame,
                text=name,
                variable=var,
                command=self._refresh_selection_status,
            ).pack(side="left", padx=(0, 8), anchor="n")

    def _browse_psdz(self) -> None:
        path = filedialog.askdirectory(title=self.tr("browse_psdz"), initialdir=self.psdz_var.get() or os.getcwd())
        if path:
            self.psdz_var.set(path)

    def _browse_xml(self, variable: tk.StringVar) -> None:
        current = variable.get() or self.svt_var.get()
        path = filedialog.askopenfilename(
            title=self.tr("browse_svt"),
            initialdir=str(Path(current).parent) if current else os.getcwd(),
            filetypes=[(self.tr("filetypes_svt"), "*.xml"), (self.tr("filetypes_all"), "*.*")],
        )
        if path:
            variable.set(path)

    def _browse_out(self) -> None:
        path = filedialog.askdirectory(title=self.tr("browse_out"), initialdir=self.out_var.get() or os.getcwd())
        if path:
            self.out_var.set(path)

    def _open_out(self) -> None:
        path = Path(self.out_var.get().strip())
        if not path.exists():
            messagebox.showinfo(self.tr("app_title"), self.tr("err_out_missing"))
            return
        webbrowser.open(path.as_uri())

    def _selected_classes(self) -> set[str]:
        return {name for name, var in self.class_vars.items() if var.get()}

    # ------------------------------------------------------------ drag & drop
    def _on_drop(self, event) -> None:
        paths = [Path(item) for item in self.tk.splitlist(event.data)]
        self.accept_paths(paths)

    def accept_paths(self, paths: list[Path]) -> None:
        """Files dropped on the window or on the exe."""
        xmls = [path for path in paths if path.is_file() and path.suffix.lower() == ".xml"]
        for path in paths:
            if path.is_dir():
                try:
                    resolve_swe_root(path)
                    self.psdz_var.set(str(path))
                    continue
                except (FileNotFoundError, OSError):
                    pass
                if self.mode == "batch":
                    xmls.extend(sorted(path.rglob("*.xml")))
        if not xmls:
            return
        if self.mode == "batch" or len(xmls) > 2:
            self.mode_var.set("batch")
            self._on_mode()
            for path in xmls:
                if str(path) not in self.batch_files:
                    self.batch_files.append(str(path))
            self._render_batch()
            return
        kinds = [detect_xml_kind(path) for path in xmls]
        if "tal" in kinds:
            self.tal_var.set(str(xmls[kinds.index("tal")]))
            self.mode_var.set("tal")
        elif len(xmls) == 2:
            first, second = sorted(xmls, key=lambda p: p.stat().st_mtime)
            self.ist_var.set(str(first))
            self.soll_var.set(str(second))
            self.mode_var.set("diff")
        elif self.mode == "diff":
            if not self.ist_var.get().strip():
                self.ist_var.set(str(xmls[0]))
            else:
                self.soll_var.set(str(xmls[0]))
        else:
            self.svt_var.set(str(xmls[0]))
            self.mode_var.set("single")
        self._on_mode()

    # ------------------------------------------------------------------ busy
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.search_btn.configure(state=state)
        for button in self.mode_buttons.values():
            button.configure(state=state)
        self.export_btn.configure(state="normal" if (not busy) and self._exportable() else "disabled")

    def log_line(self, text: str) -> None:
        self.log.insert("end", text + "\n")
        self.log.see("end")

    # ---------------------------------------------------------------- search
    def start_search(self) -> None:
        if self._busy:
            return
        psdz = self.psdz_var.get().strip()
        if not psdz or not Path(psdz).exists():
            messagebox.showerror(self.tr("app_title"), self.tr("err_psdz"))
            return
        mode = self.mode
        if mode == "single":
            files = [self.svt_var.get().strip()]
        elif mode == "diff":
            files = [self.ist_var.get().strip(), self.soll_var.get().strip()]
        elif mode == "tal":
            files = [self.tal_var.get().strip()]
        else:
            files = [item for item in self.batch_files]
            if not files:
                messagebox.showerror(self.tr("app_title"), self.tr("err_batch_empty"))
                return
        for item in files:
            if not item or not Path(item).is_file():
                messagebox.showerror(self.tr("app_title"), self.tr("err_svt"))
                return
        self._persist()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(self.tr("status_reading"))
        threading.Thread(target=self._search_worker, args=(psdz, mode, files), daemon=True).start()

    def _build_index(self, psdz: str):
        swe = resolve_swe_root(psdz)
        self.after(0, lambda: self.log_line(f"SWE: {swe}"))
        index = build_index(
            swe,
            progress=lambda n, kind: self.after(
                0, lambda n=n, kind=kind: self.status_var.set(self.tr("status_index", kind=kind, count=n))
            ),
        )
        counts = ", ".join(f"{k}={v}" for k, v in sorted(index.kind_counts.items())) or self.tr("log_empty")
        self.after(0, lambda: self.log_line(self.tr("log_index", count=index.file_count, counts=counts)))
        return index

    def _search_worker(self, psdz: str, mode: str, files: list[str]) -> None:
        try:
            if mode == "batch":
                index = self._build_index(psdz)
                results = {}
                for item in files:
                    try:
                        doc = load_document(item)
                        matches = match_svt(doc, index)
                        self._auto_nearest(matches, index)
                        results[item] = {"doc": doc, "matches": matches, "error": ""}
                    except Exception as exc:  # one bad file must not stop the batch
                        results[item] = {"doc": None, "matches": [], "error": self._format_error(exc)}
                self.after(0, lambda: self._apply_batch(index, results))
                return
            doc_ist = None
            for item in files:
                self.after(0, lambda item=item: self.log_line(f"XML: {item}"))
            if mode == "diff":
                doc_ist = load_document(files[0])
                doc = load_document(files[1])
            else:
                doc = load_document(files[0])
            self.after(0, lambda: self.log_line(self.tr("log_ecus", ecus=len(doc.ecus), parts=len(doc.parts))))
            index = self._build_index(psdz)
            self.after(0, lambda: self._apply_search(doc, doc_ist, index))
        except Exception as exc:
            error = traceback.format_exc()
            self.after(0, lambda: self._search_failed(exc, error))

    def _auto_nearest(self, matches, index) -> None:
        if not self.nearest_var.get():
            return
        for match in matches:
            if match.part.process_class and not match.found and match.other_versions:
                version = nearest_version(match)
                if version:
                    apply_override(match, index, version)

    def _apply_search(self, doc, doc_ist, index) -> None:
        classes = sorted({part.process_class for _, part in doc.parts if part.process_class})
        self._set_class_checks(classes)
        if doc_ist is not None:
            matches = match_diff(doc_ist, doc, index)
        else:
            matches = match_svt(doc, index)
        self._auto_nearest(matches, index)
        self.doc = doc
        self.doc_ist = doc_ist
        self.index = index
        self.matches = matches
        self._init_rows()
        self._render_tree()
        self._refresh_summary()
        self.log_line(self.summary_var.get())
        if summarize(matches)["found"] == 0:
            self.log_line(self.tr("log_lite"))
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        self._refresh_selection_status()
        if self._on_search_done:
            self._on_search_done()

    def _refresh_summary(self) -> None:
        stats = summarize(self.matches)
        text = self.tr(
            "summary",
            found=stats["found"],
            parts=stats["parts"],
            files=stats["unique_files"],
            missing=stats["missing"],
        )
        if stats["other"]:
            text += self.tr("summary_other", other=stats["other"])
        if stats["incomplete"]:
            text += self.tr("summary_incomplete", count=stats["incomplete"])
        if self.mode == "diff":
            text = self.tr("summary_changed", changed=stats["changed"], same=stats["same"]) + "  •  " + text
        self.summary_var.set(text)

    def _search_failed(self, exc: Exception, error: str = "") -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set(self.tr("status_search_error"))
        self.log_line(error or traceback.format_exc())
        self._set_busy(False)
        messagebox.showerror(self.tr("app_title"), self._format_error(exc))

    def _format_error(self, exc: Exception) -> str:
        if isinstance(exc, ValueError) and str(exc) == "no_ecu":
            return self.tr("err_no_ecu")
        if isinstance(exc, FileNotFoundError):
            return self.tr("err_no_swe", path=exc)
        return str(exc)

    # ------------------------------------------------------------------ rows
    def _init_rows(self) -> None:
        self.part_by_iid = {}
        self.ecu_parts = {}
        self.ecu_of = {}
        self.checked = {}
        only_changed = self.mode == "diff" and self.only_changed_var.get()
        for i, match in enumerate(self.matches):
            ecu_iid = f"e{match.ecu.index}"
            iid = f"p{i}"
            self.part_by_iid[iid] = match
            self.ecu_parts.setdefault(ecu_iid, []).append(iid)
            self.ecu_of[ecu_iid] = match.ecu
            if match.part.process_class:
                self.checked[iid] = not (only_changed and match.change == CHANGE_SAME)

    def _row(self, match) -> tuple[tuple, str]:
        part = match.part
        if not part.process_class:
            return (
                ("", part.action, "", "", "", "", "", self.tr("status_no_files"), ""),
                "nofiles",
            )
        files = ", ".join(path.name for path in match.files)
        if match.override_version and match.files:
            status = self.tr("status_override", version=match.override_version)
            tag = "override"
        elif match.found:
            status = self.tr("status_found", count=len(match.files))
            tag = "found"
            if match.incomplete:
                status += "  " + self.tr("status_incomplete", ext=match.incomplete)
                tag = "warn"
        elif match.other_versions:
            status = self.tr("status_other_list", versions=", ".join(match.other_versions[-3:]))
            tag = "other"
        else:
            status = self.tr("status_missing")
            tag = "missing"
        change = {
            CHANGE_UPDATE: self.tr("change_update"),
            CHANGE_NEW: self.tr("change_new"),
            CHANGE_SAME: self.tr("change_same"),
        }.get(match.change, "")
        if match.change == CHANGE_SAME and tag != "missing":
            tag = "same"
        ist = match.ist_version if match.change != CHANGE_NEW else "—"
        values = (MARK_OFF, part.action, part.process_class, part.ident, ist, part.version, change, status, files)
        return values, tag

    def _visible(self, match, ecu_title: str) -> bool:
        if self.mode == "diff" and self.hide_same_var.get() and match.change == CHANGE_SAME:
            return False
        needle = self.filter_var.get().strip().lower()
        if not needle:
            return True
        hay = f"{ecu_title} {match.part.label} {match.part.action}".lower()
        return needle in hay

    def _render_tree(self) -> None:
        if self.mode == "batch":
            self._render_batch()
            return
        opened = {iid for iid in self.tree.get_children("") if self.tree.item(iid, "open")}
        first = not self.tree.get_children("")
        self.tree.delete(*self.tree.get_children())
        if not self.matches:
            return
        for ecu_iid, part_iids in self.ecu_parts.items():
            ecu = self.ecu_of[ecu_iid]
            visible = [iid for iid in part_iids if self._visible(self.part_by_iid[iid], ecu.title)]
            if not visible:
                continue
            real = [iid for iid in part_iids if self.part_by_iid[iid].part.process_class]
            found = sum(1 for iid in real if self.part_by_iid[iid].found)
            changed = sum(1 for iid in real if self.part_by_iid[iid].change in (CHANGE_UPDATE, CHANGE_NEW))
            if self.mode == "diff":
                extra = self.tr("ecu_changed", count=changed) if changed else self.tr("change_same")
            else:
                extra = self.tr("status_found_ecu", found=found)
            self.tree.insert(
                "",
                "end",
                iid=ecu_iid,
                text=f"{ecu.title}   ({found}/{len(real)})",
                values=("", "", "", "", "", "", extra if self.mode == "diff" else "", "" if self.mode == "diff" else extra, ""),
                tags=("ecu",),
                open=(ecu_iid in opened) if not first else True,
            )
            for iid in visible:
                values, tag = self._row(self.part_by_iid[iid])
                match = self.part_by_iid[iid]
                text = match.part.label if match.part.process_class else match.part.action
                self.tree.insert(ecu_iid, "end", iid=iid, text=text, values=values, tags=(tag,))
        self._refresh_all_marks()

    def _mark_for(self, iid: str) -> str:
        if iid in self.ecu_parts:
            states = [self.checked[child] for child in self.ecu_parts[iid] if child in self.checked]
            if states and all(states):
                return MARK_ON
            if any(states):
                return MARK_MIX
            return MARK_OFF
        if iid not in self.checked:
            return ""
        return MARK_ON if self.checked[iid] else MARK_OFF

    def _set_item_mark(self, iid: str) -> None:
        if not self.tree.exists(iid):
            return
        values = list(self.tree.item(iid, "values"))
        if not values:
            return
        values[0] = self._mark_for(iid)
        self.tree.item(iid, values=values)

    def _refresh_all_marks(self) -> None:
        for ecu_iid in self.tree.get_children(""):
            for part_iid in self.tree.get_children(ecu_iid):
                self._set_item_mark(part_iid)
            self._set_item_mark(ecu_iid)
        self._refresh_selection_status()

    def _set_all_checked(self, value: bool) -> None:
        if self.mode == "batch":
            for item in self.batch_files:
                self.batch_checked[item] = value
            self._render_batch()
            return
        for iid in self.checked:
            self.checked[iid] = value
        self._refresh_all_marks()

    def _select_found(self) -> None:
        if self.mode == "batch":
            for item in self.batch_files:
                res = self.batch_results.get(item)
                self.batch_checked[item] = bool(res and summarize(res["matches"])["found"])
            self._render_batch()
            return
        for iid in self.checked:
            match = self.part_by_iid[iid]
            self.checked[iid] = bool(match.found) and (
                not (self.mode == "diff" and self.only_changed_var.get()) or match.change != CHANGE_SAME
            )
        self._refresh_all_marks()

    def _on_only_changed(self) -> None:
        if not self.matches:
            return
        for iid in self.checked:
            match = self.part_by_iid[iid]
            if match.change == CHANGE_SAME:
                self.checked[iid] = not self.only_changed_var.get()
        self._refresh_all_marks()

    def _on_nearest(self) -> None:
        if not self.matches or self.index is None:
            return
        for match in self.matches:
            if not match.part.process_class:
                continue
            if self.nearest_var.get() and not match.found and match.other_versions:
                version = nearest_version(match)
                if version:
                    apply_override(match, self.index, version)
            elif not self.nearest_var.get() and match.override_version:
                apply_override(match, self.index, "")
        self._render_tree()
        self._refresh_summary()

    def _toggle_all_heading(self) -> None:
        if not self.checked:
            return
        self._set_all_checked(not all(self.checked.values()))

    def _toggle_row(self, iid: str) -> None:
        if iid in self.ecu_parts:
            new_state = self._mark_for(iid) != MARK_ON
            for child in self.ecu_parts[iid]:
                if child in self.checked:
                    self.checked[child] = new_state
                    self._set_item_mark(child)
            self._set_item_mark(iid)
        elif iid in self.checked:
            self.checked[iid] = not self.checked[iid]
            self._set_item_mark(iid)
            parent = self.tree.parent(iid)
            if parent:
                self._set_item_mark(parent)
        self._refresh_selection_status()

    def _on_tree_click(self, event) -> str | None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        column = self.tree.identify_column(event.x)
        displayed = self.tree["displaycolumns"]
        if displayed in ("#all", ("#all",)):
            displayed = COLUMNS
        try:
            name = displayed[int(column.lstrip("#")) - 1]
        except (ValueError, IndexError):
            return None
        if name != "chk":
            return None
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle_row(iid)
            return "break"
        return None

    def _on_tree_space(self, event) -> str:
        for iid in self.tree.selection():
            self._toggle_row(iid)
        return "break"

    def _on_tree_double(self, event) -> str | None:
        iid = self.tree.identify_row(event.y)
        match = self.part_by_iid.get(iid)
        if match is not None and match.other_versions:
            self._version_menu(iid, event.x_root, event.y_root)
            return "break"
        return None

    def _on_tree_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self._version_menu(iid, event.x_root, event.y_root)

    def _version_menu(self, iid: str, x: int, y: int) -> None:
        match = self.part_by_iid.get(iid)
        if match is None or not match.part.process_class or self.index is None:
            return
        menu = tk.Menu(self, tearoff=0)
        if not match.other_versions:
            menu.add_command(label=self.tr("menu_no_versions"), state="disabled")
            menu.tk_popup(x, y)
            return
        wanted = match.part.version
        have = bool(self.index.files.get(match.part.key))
        choice = tk.StringVar(value=match.override_version or wanted)
        label = self.tr("menu_wanted", version=wanted) + ("" if have else "  " + self.tr("menu_not_in_db"))
        menu.add_radiobutton(label=label, value=wanted, variable=choice, command=lambda: self._set_override(iid, ""))
        menu.add_separator()
        nearest = nearest_version(match)
        for version in reversed(match.other_versions):
            suffix = "  " + self.tr("menu_nearest") if version == nearest else ""
            if match.ist_version and version == match.ist_version:
                suffix += "  (Ist)"
            menu.add_radiobutton(
                label=f"{version}{suffix}",
                value=version,
                variable=choice,
                command=lambda v=version: self._set_override(iid, v),
            )
        menu.add_separator()
        menu.add_command(label=self.tr("menu_override_note"), state="disabled")
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _set_override(self, iid: str, version: str) -> None:
        match = self.part_by_iid[iid]
        apply_override(match, self.index, version)
        if match.found and iid in self.checked:
            self.checked[iid] = True
        values, tag = self._row(match)
        if self.tree.exists(iid):
            self.tree.item(iid, values=values, tags=(tag,))
            self._set_item_mark(iid)
            self._set_item_mark(self.tree.parent(iid))
        self._refresh_summary()
        self._refresh_selection_status()

    def _selected_matches(self):
        classes = self._selected_classes()
        return [
            self.part_by_iid[iid]
            for iid, on in self.checked.items()
            if on and self.part_by_iid[iid].found and self.part_by_iid[iid].part.process_class in classes
        ]

    def _exportable(self) -> bool:
        if self.mode == "batch":
            return any(
                self.batch_checked.get(item, True) and self.batch_results.get(item, {}).get("matches")
                for item in self.batch_files
            )
        return bool(self._selected_matches()) if self.checked else False

    def _refresh_selection_status(self) -> None:
        if self.mode == "batch":
            if not self._busy:
                self.export_btn.configure(state="normal" if self._exportable() else "disabled")
            return
        if not self.checked:
            return
        blocks = len(self.ecu_parts)
        checked_blocks = sum(1 for iid in self.ecu_parts if self._mark_for(iid) != MARK_OFF)
        checked_parts = sum(1 for on in self.checked.values() if on)
        selected = self._selected_matches()
        size = 0
        seen = set()
        for match in selected:
            for path in match.files:
                if path not in seen:
                    seen.add(path)
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
        self.status_var.set(
            self.tr(
                "status_selection",
                blocks=checked_blocks,
                total_blocks=blocks,
                parts=checked_parts,
                total_parts=len(self.checked),
                ready=len(selected),
            )
            + self.tr("status_size", size=_size(size))
        )
        if not self._busy:
            self.export_btn.configure(state="normal" if selected else "disabled")

    # ---------------------------------------------------------------- export
    def _export_kwargs(self) -> dict:
        return {
            "use_hash": self.hash_var.get(),
            "hardlink": self.hardlink_var.get(),
        }

    def _meta(self) -> dict:
        meta = {}
        if self.mode == "diff" and self.doc_ist is not None:
            meta["SVT-Ist"] = str(self.doc_ist.path)
            meta["SVT-Soll"] = str(self.doc.path)
        elif self.doc is not None:
            meta["TAL" if self.doc.source == "tal" else "SVT"] = str(self.doc.path)
        if self.doc is not None and self.doc.istep:
            meta["I-Step"] = self.doc.istep
        meta["PSdZData"] = self.psdz_var.get().strip()
        return meta

    def start_export(self) -> None:
        if self._busy:
            return
        if not self._selected_classes():
            messagebox.showinfo(self.tr("app_title"), self.tr("err_no_type"))
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showerror(self.tr("app_title"), self.tr("err_out_empty"))
            return
        layout = self.layout_var.get()
        if self.mode == "batch":
            items = [
                item
                for item in self.batch_files
                if self.batch_checked.get(item, True) and self.batch_results.get(item, {}).get("matches")
            ]
            if not items:
                messagebox.showinfo(self.tr("app_title"), self.tr("err_no_found"))
                return
            self._persist()
            self._set_busy(True)
            self.progress.configure(mode="determinate", value=0, maximum=100)
            threading.Thread(target=self._batch_export_worker, args=(items, out, layout), daemon=True).start()
            return
        if not self.matches:
            return
        if not any(self.checked.values()):
            messagebox.showinfo(self.tr("app_title"), self.tr("err_no_blocks"))
            return
        found = self._selected_matches()
        if not found:
            messagebox.showinfo(self.tr("app_title"), self.tr("err_no_found"))
            return
        self._persist()
        self._set_busy(True)
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self.status_var.set(self.tr("status_copying"))
        classes = self._selected_classes()
        all_matches = [m for m in self.matches if not m.part.process_class or m.part.process_class in classes]
        threading.Thread(
            target=self._export_worker, args=(found, all_matches, out, layout, self._meta()), daemon=True
        ).start()

    def _progress_cb(self, prefix: str = ""):
        def progress(done, total, name):
            pct = int(done * 100 / total) if total else 100
            self.after(0, lambda: self._export_progress(pct, done, total, prefix + name))

        return progress

    def _export_worker(self, matches, all_matches, out: str, layout: str, meta: dict) -> None:
        try:
            copied, skipped, errors = export_matches(
                matches,
                out,
                layout,
                progress=self._progress_cb(),
                make_zip=self.zip_var.get(),
                all_matches=all_matches,
                meta=meta,
                **self._export_kwargs(),
            )
            self.after(0, lambda: self._export_done(copied, skipped, errors, out))
        except Exception as exc:
            error = traceback.format_exc()
            self.after(0, lambda: self._search_failed(exc, error))

    def _export_progress(self, pct: int, done: int, total: int, name: str) -> None:
        self.progress.configure(value=pct)
        self.status_var.set(self.tr("status_copy_file", done=done, total=total, name=name))

    def _export_done(self, copied: int, skipped: int, errors: list[str], out: str) -> None:
        self.progress.configure(value=100)
        msg = self.tr("export_done", copied=copied, skipped=skipped, errors=len(errors))
        if self.zip_var.get():
            msg += "\n" + self.tr("export_zip", path=str(Path(out).with_name(Path(out).name + ".zip")))
        self.status_var.set(msg.replace("\n", "  •  "))
        self.log_line(msg)
        self.log_line(self.tr("log_folder", path=out))
        for line in errors[:20]:
            self.log_line(self.tr("log_err", detail=line))
        self._set_busy(False)
        if self._on_export_done:
            self._on_export_done()
            return
        if errors:
            messagebox.showwarning(self.tr("app_title"), msg)
        else:
            messagebox.showinfo(self.tr("app_title"), msg + f"\n\n{out}")

    # ------------------------------------------------------ missing / report
    def _report_matches(self):
        classes = self._selected_classes()
        return [m for m in self.matches if m.part.process_class in classes]

    def show_missing_dialog(self) -> tk.Toplevel | None:
        if not self.matches:
            messagebox.showinfo(self.tr("app_title"), self.tr("err_search_first"))
            return None
        return MissingDialog(self)

    def save_report_csv(self) -> None:
        if not self.matches:
            messagebox.showinfo(self.tr("app_title"), self.tr("err_search_first"))
            return
        path = filedialog.asksaveasfilename(
            title=self.tr("btn_report_csv"),
            defaultextension=".csv",
            initialfile="svt_report.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if path:
            Path(path).write_text(full_report_csv(self.matches), encoding="utf-8-sig")
            self.log_line(self.tr("log_saved", path=path))

    # ----------------------------------------------------------------- batch
    def _batch_add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title=self.tr("batch_add"),
            filetypes=[(self.tr("filetypes_svt"), "*.xml"), (self.tr("filetypes_all"), "*.*")],
        )
        for path in paths:
            if path not in self.batch_files:
                self.batch_files.append(path)
        self._render_batch()

    def _batch_add_folder(self) -> None:
        folder = filedialog.askdirectory(title=self.tr("batch_add_folder"))
        if not folder:
            return
        for path in sorted(Path(folder).rglob("*.xml")):
            if str(path) not in self.batch_files:
                self.batch_files.append(str(path))
        self._render_batch()

    def _batch_remove(self) -> None:
        for iid in self.batch_tree.selection():
            item = self.batch_files[int(iid[1:])]
            self.batch_results.pop(item, None)
            self.batch_checked.pop(item, None)
        keep = [
            item for i, item in enumerate(self.batch_files) if f"b{i}" not in set(self.batch_tree.selection())
        ]
        self.batch_files = keep
        self._render_batch()

    def _batch_folder_names(self) -> dict[str, str]:
        used: set[str] = set()
        return {item: batch_name(Path(item), used) for item in self.batch_files}

    def _render_batch(self) -> None:
        self.batch_tree.delete(*self.batch_tree.get_children())
        names = self._batch_folder_names()
        out = Path(self.out_var.get().strip() or ".")
        needle = self.filter_var.get().strip().lower()
        for i, item in enumerate(self.batch_files):
            if needle and needle not in item.lower():
                continue
            res = self.batch_results.get(item)
            checked = self.batch_checked.setdefault(item, True)
            folder = str(out) if self.batch_shared_var.get() else str(out / names[item])
            shown = f"{Path(item).parent.name}/{Path(item).name}"
            folder = folder if self.batch_shared_var.get() else f"…/{names[item]}"
            if res is None:
                values = (MARK_ON if checked else MARK_OFF, shown, "", "", "", "", self.tr("batch_waiting"), folder)
                tag = ""
            elif res["error"]:
                values = (MARK_OFF, shown, "", "", "", "", res["error"], "")
                tag = "missing"
            else:
                stats = summarize(res["matches"])
                state = res.get("state") or (
                    self.tr("batch_has_other") if stats["other"] else self.tr("batch_ready")
                )
                values = (
                    MARK_ON if checked else MARK_OFF,
                    shown,
                    f"{len(res['doc'].ecus)} / {stats['parts']}",
                    stats["found"],
                    stats["other"],
                    stats["missing"],
                    state,
                    folder,
                )
                tag = "other" if stats["other"] else "found"
            self.batch_tree.insert("", "end", iid=f"b{i}", values=values, tags=(tag,) if tag else ())
        self._refresh_selection_status()

    def _on_batch_click(self, event) -> str | None:
        if self.batch_tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.batch_tree.identify_column(event.x) != "#1":
            return None
        iid = self.batch_tree.identify_row(event.y)
        if iid:
            item = self.batch_files[int(iid[1:])]
            self.batch_checked[item] = not self.batch_checked.get(item, True)
            self._render_batch()
            return "break"
        return None

    def _batch_toggle_all(self) -> None:
        value = not all(self.batch_checked.get(item, True) for item in self.batch_files)
        self._set_all_checked(value)

    def _apply_batch(self, index, results) -> None:
        self.index = index
        self.batch_results = results
        classes = sorted(
            {
                part.process_class
                for res in results.values()
                if res["doc"] is not None
                for _, part in res["doc"].parts
                if part.process_class
            }
        )
        if classes:
            self._set_class_checks(classes)
        total = sum(summarize(res["matches"])["found"] for res in results.values())
        self.summary_var.set(self.tr("batch_summary", cars=len(results), found=total))
        self.log_line(self.summary_var.get())
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        self._render_batch()
        self.status_var.set(self.tr("status_search_done", detail=self.summary_var.get()))
        if self._on_search_done:
            self._on_search_done()

    def _batch_export_worker(self, items: list[str], out: str, layout: str) -> None:
        try:
            import csv
            import io

            names = self._batch_folder_names()
            classes = self._selected_classes()
            shared = self.batch_shared_var.get()
            out_root = Path(out)
            totals = [0, 0, 0]
            rows = []
            all_errors: list[str] = []
            for n, item in enumerate(items, start=1):
                res = self.batch_results[item]
                matches = [m for m in res["matches"] if m.part.process_class in classes]
                found = [m for m in matches if m.found]
                dest = out_root if shared else out_root / names[item]
                meta = {"SVT": item, "PSdZData": self.psdz_var.get().strip()}
                copied, skipped, errors = export_matches(
                    found,
                    dest,
                    layout,
                    progress=self._progress_cb(f"[{n}/{len(items)}] "),
                    make_zip=self.zip_var.get() and not shared,
                    all_matches=matches,
                    meta=meta,
                    **self._export_kwargs(),
                )
                totals[0] += copied
                totals[1] += skipped
                totals[2] += len(errors)
                all_errors += errors
                stats = summarize(matches)
                rows.append([item, dest, stats["parts"], stats["found"], stats["other"], stats["missing"], copied, skipped, len(errors)])
                res["state"] = self.tr("batch_done", copied=copied)
            if shared and self.zip_var.get():
                zip_folder(out_root)
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(["svt", "folder", "parts", "found", "other_versions", "missing", "copied", "skipped", "errors"])
            writer.writerows(rows)
            out_root.mkdir(parents=True, exist_ok=True)
            (out_root / "batch_report.csv").write_text(buffer.getvalue(), encoding="utf-8-sig")
            self.after(0, lambda: (self._render_batch(), self._export_done(totals[0], totals[1], all_errors, out)))
        except Exception as exc:
            error = traceback.format_exc()
            self.after(0, lambda: self._search_failed(exc, error))

    # ---------------------------------------------------------------- config
    def _persist(self) -> None:
        if not self.remember_paths:
            return
        save_config(
            {
                "mode": self.mode,
                "psdz": self.psdz_var.get().strip(),
                "svt": self.svt_var.get().strip(),
                "svt_ist": self.ist_var.get().strip(),
                "svt_soll": self.soll_var.get().strip(),
                "tal": self.tal_var.get().strip(),
                "batch": self.batch_files,
                "out": self.out_var.get().strip(),
                "layout": self.layout_var.get(),
                "language": self._lang,
                "options": {
                    "only_changed": self.only_changed_var.get(),
                    "hide_same": self.hide_same_var.get(),
                    "nearest": self.nearest_var.get(),
                    "zip": self.zip_var.get(),
                    "hardlink": self.hardlink_var.get(),
                    "hash": self.hash_var.get(),
                    "batch_shared": self.batch_shared_var.get(),
                },
            }
        )

    def _on_close(self) -> None:
        self._persist()
        self.destroy()


class MissingDialog(tk.Toplevel):
    FORMATS = ("forum", "names", "csv")

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.transient(app)
        self.geometry("720x560")
        self.minsize(560, 420)
        tr = app.tr
        missing = missing_matches(app._report_matches())
        self.title(tr("missing_title", count=len(missing)))
        self.fmt_var = tk.StringVar(value="forum")
        self.other_var = tk.BooleanVar(value=True)
        self.car_var = tk.StringVar(value=self._guess_car())
        self.istep_var = tk.StringVar(value=app.doc.istep if app.doc is not None else "")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Label(row, text=tr("missing_format")).pack(side="left")
        for value in self.FORMATS:
            ttk.Radiobutton(
                row, text=tr(f"fmt_{value}"), value=value, variable=self.fmt_var, style="Toolbutton", command=self.refresh
            ).pack(side="left", padx=(6 if value == "forum" else 0, 0), ipadx=6)
        ttk.Checkbutton(row, text=tr("missing_include_other"), variable=self.other_var, command=self.refresh).pack(
            side="left", padx=(16, 0)
        )
        meta = ttk.Frame(body)
        meta.pack(fill="x", pady=(10, 0))
        ttk.Label(meta, text=tr("missing_car")).pack(side="left")
        ttk.Entry(meta, textvariable=self.car_var, width=28).pack(side="left", padx=(6, 16))
        ttk.Label(meta, text="I-Step").pack(side="left")
        ttk.Entry(meta, textvariable=self.istep_var, width=20).pack(side="left", padx=(6, 0))
        self.car_var.trace_add("write", lambda *_: self.refresh())
        self.istep_var.trace_add("write", lambda *_: self.refresh())
        ttk.Label(body, text=tr("missing_preview")).pack(anchor="w", pady=(10, 4))
        text_wrap = ttk.Frame(body)
        text_wrap.pack(fill="both", expand=True)
        self.text = tk.Text(text_wrap, wrap="none", font=("Consolas", 9), height=16)
        scroll = ttk.Scrollbar(text_wrap, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text=tr("btn_copy"), command=self.copy).pack(side="right")
        ttk.Button(buttons, text=tr("btn_save_csv"), command=lambda: self.save("csv")).pack(side="right", padx=(0, 6))
        ttk.Button(buttons, text=tr("btn_save_txt"), command=lambda: self.save("txt")).pack(side="right", padx=(0, 6))
        self.status = ttk.Label(buttons, text="")
        self.status.pack(side="left")
        self.refresh()

    def _guess_car(self) -> str:
        doc = self.app.doc
        if doc is None:
            return ""
        folder = doc.path.parent.name
        return folder if folder and folder.lower() not in ("svt", "demo") else ""

    def content(self, fmt: str | None = None) -> str:
        psdz = Path(self.app.psdz_var.get().strip()).name
        return missing_report(
            self.app._report_matches(),
            fmt or self.fmt_var.get(),
            include_other=self.other_var.get(),
            car=self.car_var.get().strip(),
            istep=self.istep_var.get().strip(),
            psdz_name=psdz,
        )

    def refresh(self) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self.content())

    def copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))
        self.status.configure(text=self.app.tr("copied"))

    def save(self, kind: str) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=f".{kind}",
            initialfile=f"missing_swe.{kind}",
            filetypes=[(kind.upper(), f"*.{kind}")],
        )
        if not path:
            return
        text = self.content("csv") if kind == "csv" else self.text.get("1.0", "end-1c")
        Path(path).write_text(text, encoding="utf-8-sig" if kind == "csv" else "utf-8")
        self.status.configure(text=self.app.tr("log_saved", path=path))


# -------------------------------------------------------------------- CLI
def run_cli(args) -> None:
    index = build_index(resolve_swe_root(args.psdz))
    kwargs = {"use_hash": args.hash, "hardlink": args.hardlink, "make_zip": args.zip}

    def pick(matches):
        if args.nearest:
            for match in matches:
                if match.part.process_class and not match.found and match.other_versions:
                    apply_override(match, index, nearest_version(match))
        wanted = [m for m in matches if m.part.process_class and m.part.process_class not in HARDWARE_CLASSES]
        if args.soll and not args.all_parts:
            return wanted, [m for m in wanted if m.found and m.change != CHANGE_SAME]
        return wanted, [m for m in wanted if m.found]

    jobs = []
    if args.batch:
        used: set[str] = set()
        for item in args.batch:
            jobs.append((Path(item), load_document(item), None, Path(args.out) / batch_name(Path(item), used)))
    elif args.tal:
        jobs.append((Path(args.tal), load_document(args.tal), None, Path(args.out)))
    elif args.soll:
        jobs.append((Path(args.soll), load_document(args.soll), load_document(args.svt), Path(args.out)))
    else:
        jobs.append((Path(args.svt), load_document(args.svt), None, Path(args.out)))

    failed = False
    for path, doc, ist, dest in jobs:
        matches = match_diff(ist, doc, index) if ist is not None else match_svt(doc, index)
        report, export = pick(matches)
        stats = summarize(report)
        print(f"{path.name}: ECU {len(doc.ecus)}, found {stats['found']}/{stats['parts']}, missing {stats['missing']}")
        if ist is not None:
            print(f"  changed {stats['changed']}, unchanged {stats['same']}")
        copied, skipped, errors = export_matches(
            export, dest, args.layout, all_matches=report, meta={"Source": str(path)}, **kwargs
        )
        print(f"  -> {dest}: copied {copied}, skipped {skipped}, errors {len(errors)}")
        for err in errors:
            print("ERR", err)
        failed = failed or bool(errors)
        if args.missing_report:
            target = Path(args.missing_report)
            if len(jobs) > 1:
                target = dest / target.name
            fmt = "csv" if target.suffix.lower() == ".csv" else "forum"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(missing_report(report, fmt, istep=doc.istep, psdz_name=Path(args.psdz).name), encoding="utf-8")
            print(f"  missing list: {target}")
    if failed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} {APP_VERSION}")
    parser.add_argument("files", nargs="*", help="SVT/TAL XML files or a PSdZData folder (drop onto the exe)")
    parser.add_argument("--svt", help="SVT XML (with --soll: the current SVT-Ist)")
    parser.add_argument("--soll", help="Target SVT-Soll: export only what changes against --svt")
    parser.add_argument("--tal", help="TAL XML from E-Sys")
    parser.add_argument("--batch", nargs="+", help="Several SVT files, one subfolder per car")
    parser.add_argument("--psdz", help="PSdZData folder")
    parser.add_argument("--out", help="Export folder")
    parser.add_argument("--layout", choices=list(LAYOUTS), default="psdzdata")
    parser.add_argument("--all-parts", action="store_true", help="With --soll: also export unchanged parts")
    parser.add_argument("--nearest", action="store_true", help="Use the nearest version when the exact one is missing")
    parser.add_argument("--zip", action="store_true", help="Also pack the export folder into a .zip")
    parser.add_argument("--hardlink", action="store_true", help="Hard links instead of copies (same drive)")
    parser.add_argument("--hash", action="store_true", help="Compare existing files by SHA-1, not only size")
    parser.add_argument("--missing-report", help="Write the missing-files list (.txt forum text or .csv)")
    parser.add_argument("--lang", choices=list(LANGS), help="UI language (default: system)")
    parser.add_argument("--demo", action="store_true", help="Open GUI with demo data")
    args = parser.parse_args()
    if args.psdz and args.out and (args.svt or args.tal or args.batch):
        run_cli(args)
        return
    app = App(lang=args.lang)
    if args.demo:
        app.load_demo_paths()
        app.after(200, app.start_search)
    elif args.files:
        app.after(100, lambda: app.accept_paths([Path(item) for item in args.files]))
    app.mainloop()


if __name__ == "__main__":
    main()
