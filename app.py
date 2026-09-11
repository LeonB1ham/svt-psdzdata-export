from __future__ import annotations

import argparse
import json
import os
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from engine import (
    HARDWARE_CLASSES,
    build_index,
    export_matches,
    match_svt,
    parse_svt,
    resolve_swe_root,
    summarize,
)
from i18n import LANG_NAMES, LANGS, NAME_TO_LANG, normalize_language, system_language, translate
from version import APP_VERSION

APP_TITLE = "SVT → PSdZData Export"
CONFIG_PATH = Path(__file__).with_name("svt_export.json")
DEFAULT_PSDZ = Path(r"C:\PSdZData 4.60.11 Lite")
DEFAULT_SVT = DEFAULT_PSDZ / "SVT" / "svt for soft targ.xml"
DEFAULT_OUT = Path.home() / "Desktop" / "SVT_Export"

LAYOUTS = ("psdzdata", "swe", "ecu", "flat")
LAYOUT_KEYS = {
    "psdzdata": "layout_psdzdata",
    "swe": "layout_swe",
    "ecu": "layout_ecu",
    "flat": "layout_flat",
}

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


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(960, 620)
        self.matches = []
        self.index = None
        self.doc = None
        self._busy = False
        self.class_vars: dict[str, tk.BooleanVar] = {}
        self.row_matches: dict[str, object] = {}
        self.checked: dict[str, bool] = {}
        self.remember_paths = True
        self._on_search_done = None
        cfg = load_config()
        self._lang = normalize_language(cfg.get("language")) if cfg.get("language") in LANGS else system_language()

        self.psdz_var = tk.StringVar(value=cfg.get("psdz", str(DEFAULT_PSDZ if DEFAULT_PSDZ.exists() else "")))
        self.svt_var = tk.StringVar(value=cfg.get("svt", str(DEFAULT_SVT if DEFAULT_SVT.exists() else "")))
        self.out_var = tk.StringVar(value=cfg.get("out", str(DEFAULT_OUT)))
        self.layout_var = tk.StringVar(value=cfg.get("layout", "psdzdata"))
        self.lang_var = tk.StringVar(value=LANG_NAMES[self._lang])
        self.status_var = tk.StringVar(value="")
        self.summary_var = tk.StringVar(value="")

        self._build()
        self._apply_language()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def tr(self, key: str, **kwargs) -> str:
        return translate(self._lang, key, **kwargs)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        self.paths_frame = ttk.LabelFrame(root, text="", padding=8)
        self.paths_frame.pack(fill="x")
        self.lbl_psdz, self.btn_browse_psdz = self._path_row(self.paths_frame, 0, self.psdz_var, self._browse_psdz)
        self.lbl_svt, self.btn_browse_svt = self._path_row(self.paths_frame, 1, self.svt_var, self._browse_svt)
        self.lbl_out, self.btn_browse_out = self._path_row(self.paths_frame, 2, self.out_var, self._browse_out)
        lang_box = ttk.Frame(self.paths_frame)
        lang_box.grid(row=0, column=3, rowspan=3, sticky="ne", padx=(16, 0))
        self.lbl_lang = ttk.Label(lang_box)
        self.lbl_lang.pack(anchor="e")
        self.lang_combo = ttk.Combobox(
            lang_box,
            textvariable=self.lang_var,
            values=list(LANG_NAMES.values()),
            state="readonly",
            width=14,
        )
        self.lang_combo.pack(anchor="e", pady=(4, 0))
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language)

        opts = ttk.Frame(root)
        opts.pack(fill="x", pady=(8, 0))

        self.types_frame = ttk.LabelFrame(opts, text="", padding=8)
        self.types_frame.pack(side="left", fill="x", expand=True)

        self.layout_frame = ttk.LabelFrame(opts, text="", padding=8)
        self.layout_frame.pack(side="left", fill="y", padx=(8, 0))
        self.layout_buttons: dict[str, ttk.Radiobutton] = {}
        for value in LAYOUTS:
            button = ttk.Radiobutton(self.layout_frame, text="", value=value, variable=self.layout_var)
            button.pack(anchor="w")
            self.layout_buttons[value] = button

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=8)
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
        ttk.Label(actions, textvariable=self.summary_var).pack(side="right")

        panes = ttk.Panedwindow(root, orient="vertical")
        panes.pack(fill="both", expand=True)

        tree_wrap = ttk.Frame(panes)
        cols = ("chk", "cls", "ident", "ver", "status", "files")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("chk", text="✓", command=self._toggle_all_heading)
        self.tree.heading("ident", text="ID")
        self.tree.column("#0", width=300, stretch=True)
        self.tree.column("chk", width=36, anchor="center", stretch=False)
        self.tree.column("cls", width=70, anchor="center")
        self.tree.column("ident", width=100, anchor="center")
        self.tree.column("ver", width=110, anchor="center")
        self.tree.column("status", width=160)
        self.tree.column("files", width=340)
        yscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.tree.tag_configure("found", foreground="#0a7a32")
        self.tree.tag_configure("other", foreground="#b36b00")
        self.tree.tag_configure("missing", foreground="#b42318")
        self.tree.tag_configure("ecu", font=("Segoe UI", 9, "bold"))
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)

        log_wrap = ttk.Frame(panes)
        self.log = tk.Text(log_wrap, height=8, wrap="word", font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        panes.add(tree_wrap, weight=4)
        panes.add(log_wrap, weight=1)

        bottom = ttk.Frame(root)
        bottom.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w", pady=(4, 0))

        self._set_class_checks(["BTLD", "SWFL", "SWFK", "CAFD", "HWEL", "HWAP"])

    def load_demo_paths(self) -> Path:
        demo = Path(__file__).resolve().parent / "demo"
        self.remember_paths = False
        self.psdz_var.set(str(demo))
        self.svt_var.set(str(demo / "sample-svt.xml"))
        self.out_var.set(str(demo / "out"))
        return demo

    def _path_row(self, parent, row: int, variable: tk.StringVar, command):
        label = ttk.Label(parent, width=12)
        label.grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        button = ttk.Button(parent, command=command)
        button.grid(row=row, column=2, pady=2)
        parent.columnconfigure(1, weight=1)
        return label, button

    def _on_language(self, _event=None) -> None:
        lang = NAME_TO_LANG.get(self.lang_var.get())
        if not lang or lang == self._lang:
            return
        self._lang = lang
        self._apply_language()
        self._persist()

    def _apply_language(self) -> None:
        self.title(f"{self.tr('app_title')} {APP_VERSION}")
        self.paths_frame.configure(text=self.tr("paths"))
        self.lbl_psdz.configure(text=self.tr("path_psdz"))
        self.lbl_svt.configure(text=self.tr("path_svt"))
        self.lbl_out.configure(text=self.tr("path_out"))
        self.btn_browse_psdz.configure(text=self.tr("browse"))
        self.btn_browse_svt.configure(text=self.tr("browse"))
        self.btn_browse_out.configure(text=self.tr("browse"))
        self.lbl_lang.configure(text=self.tr("language"))
        self.types_frame.configure(text=self.tr("export_types"))
        self.layout_frame.configure(text=self.tr("export_layout"))
        for value, button in self.layout_buttons.items():
            button.configure(text=self.tr(LAYOUT_KEYS[value]))
        self.search_btn.configure(text=self.tr("search"))
        self.export_btn.configure(text=self.tr("export_selected"))
        self.open_btn.configure(text=self.tr("open_export"))
        self.lbl_blocks.configure(text=self.tr("blocks"))
        self.btn_all.configure(text=self.tr("select_all"))
        self.btn_none.configure(text=self.tr("select_none"))
        self.btn_found.configure(text=self.tr("select_found"))
        self.tree.heading("#0", text=self.tr("col_ecu"))
        self.tree.heading("cls", text=self.tr("col_class"))
        self.tree.heading("ver", text=self.tr("col_ver"))
        self.tree.heading("status", text=self.tr("col_status"))
        self.tree.heading("files", text=self.tr("col_files"))
        if self.matches:
            checked = dict(self.checked)
            self._fill_tree(self.matches)
            for iid in self.checked:
                if iid in checked:
                    self.checked[iid] = checked[iid]
            self._refresh_all_marks()
            self._refresh_summary()
        elif not self._busy:
            self.status_var.set(self.tr("status_ready"))

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
            ).pack(side="left", padx=(0, 8))

    def _browse_psdz(self) -> None:
        path = filedialog.askdirectory(title=self.tr("browse_psdz"), initialdir=self.psdz_var.get() or os.getcwd())
        if path:
            self.psdz_var.set(path)

    def _browse_svt(self) -> None:
        path = filedialog.askopenfilename(
            title=self.tr("browse_svt"),
            initialdir=str(Path(self.svt_var.get()).parent) if self.svt_var.get() else os.getcwd(),
            filetypes=[(self.tr("filetypes_svt"), "*.xml"), (self.tr("filetypes_all"), "*.*")],
        )
        if path:
            self.svt_var.set(path)

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

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.search_btn.configure(state=state)
        self.export_btn.configure(state="normal" if (not busy) and self._exportable() else "disabled")

    def log_line(self, text: str) -> None:
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def start_search(self) -> None:
        if self._busy:
            return
        psdz = self.psdz_var.get().strip()
        svt = self.svt_var.get().strip()
        if not psdz or not Path(psdz).exists():
            messagebox.showerror(self.tr("app_title"), self.tr("err_psdz"))
            return
        if not svt or not Path(svt).is_file():
            messagebox.showerror(self.tr("app_title"), self.tr("err_svt"))
            return
        self._persist()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(self.tr("status_reading"))
        threading.Thread(target=self._search_worker, args=(psdz, svt), daemon=True).start()

    def _search_worker(self, psdz: str, svt: str) -> None:
        try:
            self.after(0, lambda: self.log_line(f"SVT: {svt}"))
            doc = parse_svt(svt)
            classes = sorted({part.process_class for _, part in doc.parts})
            self.after(0, lambda: self.log_line(self.tr("log_ecus", ecus=len(doc.ecus), parts=len(doc.parts))))
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
            self.after(0, lambda: self._apply_search(doc, index, classes))
        except Exception as exc:
            self.after(0, lambda: self._search_failed(exc))

    def _apply_search(self, doc, index, classes) -> None:
        self._set_class_checks(classes)
        matches = match_svt(doc, index)
        self.doc = doc
        self.index = index
        self.matches = matches
        self._fill_tree(matches)
        self._refresh_summary()
        self.log_line(self.summary_var.get())
        if summarize(matches)["found"] == 0:
            self.log_line(self.tr("log_lite"))
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._refresh_selection_status()
        self.status_var.set(self.tr("status_search_done", detail=self.status_var.get()))
        self._set_busy(False)
        if self._on_search_done:
            self._on_search_done()

    def _refresh_summary(self) -> None:
        stats = summarize(self.matches)
        extra = self.tr("summary_other", other=stats["other"]) if stats["other"] else ""
        self.summary_var.set(
            self.tr(
                "summary",
                found=stats["found"],
                parts=stats["parts"],
                files=stats["unique_files"],
                missing=stats["missing"],
            )
            + extra
        )

    def _search_failed(self, exc: Exception) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set(self.tr("status_search_error"))
        self.log_line(traceback.format_exc())
        self._set_busy(False)
        messagebox.showerror(self.tr("app_title"), self._format_error(exc))

    def _format_error(self, exc: Exception) -> str:
        if isinstance(exc, ValueError) and str(exc) == "no_ecu":
            return self.tr("err_no_ecu")
        if isinstance(exc, FileNotFoundError):
            return self.tr("err_no_swe", path=exc)
        return str(exc)

    def _fill_tree(self, matches) -> None:
        self.tree.delete(*self.tree.get_children())
        self.row_matches = {}
        self.checked = {}
        by_ecu: dict[int, list] = {}
        for match in matches:
            by_ecu.setdefault(match.ecu.index, []).append(match)
        for ecu_index, items in by_ecu.items():
            ecu = items[0].ecu
            found = sum(1 for item in items if item.found)
            parent = self.tree.insert(
                "",
                "end",
                iid=f"ecu_{ecu_index}",
                text=f"{ecu.title}   ({found}/{len(items)})",
                values=(MARK_ON, "", "", "", self.tr("status_found_ecu", found=found), ""),
                tags=("ecu",),
                open=found > 0,
            )
            for part_index, match in enumerate(items):
                if match.found:
                    status = self.tr("status_found", count=len(match.files))
                    files = ", ".join(path.name for path in match.files)
                    tag = "found"
                elif match.other_versions:
                    status = self.tr("status_other")
                    files = ", ".join(match.other_versions[:8])
                    tag = "other"
                else:
                    status = self.tr("status_missing")
                    files = ""
                    tag = "missing"
                iid = f"part_{ecu_index}_{part_index}"
                self.row_matches[iid] = match
                self.checked[iid] = True
                self.tree.insert(
                    parent,
                    "end",
                    iid=iid,
                    text=match.part.label,
                    values=(MARK_ON, match.part.process_class, match.part.ident, match.part.version, status, files),
                    tags=(tag,),
                )
        self._refresh_selection_status()

    def _mark_for(self, iid: str) -> str:
        children = self.tree.get_children(iid)
        if not children:
            return MARK_ON if self.checked.get(iid, False) else MARK_OFF
        states = [self.checked.get(child, False) for child in children]
        if states and all(states):
            return MARK_ON
        if any(states):
            return MARK_MIX
        return MARK_OFF

    def _set_item_mark(self, iid: str) -> None:
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
        if not self.row_matches:
            return
        for iid in self.row_matches:
            self.checked[iid] = value
        self._refresh_all_marks()

    def _select_found(self) -> None:
        if not self.row_matches:
            return
        for iid, match in self.row_matches.items():
            self.checked[iid] = bool(match.found)
        self._refresh_all_marks()

    def _toggle_all_heading(self) -> None:
        if not self.row_matches:
            return
        self._set_all_checked(not all(self.checked.values()))

    def _toggle_row(self, iid: str) -> None:
        children = self.tree.get_children(iid)
        if children:
            new_state = self._mark_for(iid) is not MARK_ON
            for child in children:
                self.checked[child] = new_state
                self._set_item_mark(child)
            self._set_item_mark(iid)
        elif iid in self.row_matches:
            self.checked[iid] = not self.checked.get(iid, False)
            self._set_item_mark(iid)
            parent = self.tree.parent(iid)
            if parent:
                self._set_item_mark(parent)
        self._refresh_selection_status()

    def _on_tree_click(self, event) -> str | None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#1":
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

    def _selected_matches(self):
        classes = self._selected_classes()
        return [
            match
            for iid, match in self.row_matches.items()
            if self.checked.get(iid) and match.found and match.part.process_class in classes
        ]

    def _exportable(self) -> bool:
        return bool(self._selected_matches()) if self.row_matches else False

    def _refresh_selection_status(self) -> None:
        if not self.row_matches:
            return
        blocks = 0
        checked_blocks = 0
        for ecu_iid in self.tree.get_children(""):
            blocks += 1
            if self._mark_for(ecu_iid) != MARK_OFF:
                checked_blocks += 1
        checked_parts = sum(1 for iid in self.row_matches if self.checked.get(iid))
        ready = len(self._selected_matches())
        self.status_var.set(
            self.tr(
                "status_selection",
                blocks=checked_blocks,
                total_blocks=blocks,
                parts=checked_parts,
                total_parts=len(self.row_matches),
                ready=ready,
            )
        )
        if not self._busy:
            self.export_btn.configure(state="normal" if ready else "disabled")

    def start_export(self) -> None:
        if self._busy or not self.matches:
            return
        if not self._selected_classes():
            messagebox.showinfo(self.tr("app_title"), self.tr("err_no_type"))
            return
        if not any(self.checked.values()):
            messagebox.showinfo(self.tr("app_title"), self.tr("err_no_blocks"))
            return
        found = self._selected_matches()
        if not found:
            messagebox.showinfo(self.tr("app_title"), self.tr("err_no_found"))
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showerror(self.tr("app_title"), self.tr("err_out_empty"))
            return
        self._persist()
        self._set_busy(True)
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self.status_var.set(self.tr("status_copying"))
        layout = self.layout_var.get()
        threading.Thread(target=self._export_worker, args=(found, out, layout), daemon=True).start()

    def _export_worker(self, matches, out: str, layout: str) -> None:
        try:
            def progress(done, total, name):
                pct = int(done * 100 / total) if total else 100
                self.after(0, lambda: self._export_progress(pct, done, total, name))

            copied, skipped, errors = export_matches(matches, out, layout, progress=progress)
            self.after(0, lambda: self._export_done(copied, skipped, errors, out))
        except Exception as exc:
            self.after(0, lambda: self._search_failed(exc))

    def _export_progress(self, pct: int, done: int, total: int, name: str) -> None:
        self.progress.configure(value=pct)
        self.status_var.set(self.tr("status_copy_file", done=done, total=total, name=name))

    def _export_done(self, copied: int, skipped: int, errors: list[str], out: str) -> None:
        self.progress.configure(value=100)
        msg = self.tr("export_done", copied=copied, skipped=skipped, errors=len(errors))
        self.status_var.set(msg)
        self.log_line(msg)
        self.log_line(self.tr("log_folder", path=out))
        if errors:
            for line in errors[:20]:
                self.log_line(self.tr("log_err", detail=line))
        self._set_busy(False)
        if errors:
            messagebox.showwarning(self.tr("app_title"), msg)
        else:
            messagebox.showinfo(self.tr("app_title"), msg + f"\n\n{out}")

    def _persist(self) -> None:
        if not self.remember_paths:
            return
        save_config(
            {
                "psdz": self.psdz_var.get().strip(),
                "svt": self.svt_var.get().strip(),
                "out": self.out_var.get().strip(),
                "layout": self.layout_var.get(),
                "language": self._lang,
            }
        )

    def _on_close(self) -> None:
        self._persist()
        self.destroy()


def run_cli(svt: str, psdz: str, out: str, layout: str) -> None:
    doc = parse_svt(svt)
    index = build_index(resolve_swe_root(psdz))
    matches = [item for item in match_svt(doc, index) if item.found]
    stats = summarize(matches)
    print(f"ECU: {len(doc.ecus)}, found: {stats['found']}, files: {stats['unique_files']}")
    copied, skipped, errors = export_matches(matches, out, layout)
    print(f"Copied: {copied}, skipped: {skipped}, errors: {len(errors)}")
    for err in errors:
        print("ERR", err)
    if errors:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} {APP_VERSION}")
    parser.add_argument("--svt", help="SVT XML")
    parser.add_argument("--psdz", help="PSdZData folder")
    parser.add_argument("--out", help="Export folder")
    parser.add_argument("--layout", choices=list(LAYOUTS), default="psdzdata")
    parser.add_argument("--lang", choices=list(LANGS), help="UI language (default: system)")
    parser.add_argument("--demo", action="store_true", help="Open GUI with demo/sample-svt.xml")
    args = parser.parse_args()
    if args.svt and args.psdz and args.out:
        run_cli(args.svt, args.psdz, args.out, args.layout)
        return
    app = App()
    if args.lang:
        app._lang = args.lang
        app.lang_var.set(LANG_NAMES[args.lang])
        app._apply_language()
    if args.demo:
        app.load_demo_paths()
        app.after(200, app.start_search)
    app.mainloop()


if __name__ == "__main__":
    main()
