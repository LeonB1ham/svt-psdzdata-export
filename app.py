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

APP_TITLE = "SVT → PSdZData Export"
CONFIG_PATH = Path(__file__).with_name("svt_export.json")
DEFAULT_PSDZ = Path(r"C:\PSdZData 4.60.11 Lite")
DEFAULT_SVT = DEFAULT_PSDZ / "SVT" / "svt for soft targ.xml"
DEFAULT_OUT = Path.home() / "Desktop" / "SVT_Export"

LAYOUTS = (
    ("psdzdata", "psdzdata/swe/...  (для E-Sys)"),
    ("swe", "swe/btld, swe/swfl, ..."),
    ("ecu", "по ECU"),
    ("flat", "все файлы в одну папку"),
)

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
        cfg = load_config()

        self.psdz_var = tk.StringVar(value=cfg.get("psdz", str(DEFAULT_PSDZ if DEFAULT_PSDZ.exists() else "")))
        self.svt_var = tk.StringVar(value=cfg.get("svt", str(DEFAULT_SVT if DEFAULT_SVT.exists() else "")))
        self.out_var = tk.StringVar(value=cfg.get("out", str(DEFAULT_OUT)))
        self.layout_var = tk.StringVar(value=cfg.get("layout", "psdzdata"))
        self.status_var = tk.StringVar(value="Выберите PSdZData и SVT, затем нажмите «Найти».")

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        paths = ttk.LabelFrame(root, text="Пути", padding=8)
        paths.pack(fill="x")
        self._path_row(paths, 0, "PSdZData", self.psdz_var, self._browse_psdz)
        self._path_row(paths, 1, "SVT XML", self.svt_var, self._browse_svt)
        self._path_row(paths, 2, "Экспорт", self.out_var, self._browse_out)

        opts = ttk.Frame(root)
        opts.pack(fill="x", pady=(8, 0))

        types = ttk.LabelFrame(opts, text="Экспортировать типы", padding=8)
        types.pack(side="left", fill="x", expand=True)
        self.types_frame = types

        layout = ttk.LabelFrame(opts, text="Структура экспорта", padding=8)
        layout.pack(side="left", fill="y", padx=(8, 0))
        for value, label in LAYOUTS:
            ttk.Radiobutton(layout, text=label, value=value, variable=self.layout_var).pack(anchor="w")

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=8)
        self.search_btn = ttk.Button(actions, text="Найти файлы", command=self.start_search)
        self.search_btn.pack(side="left")
        self.export_btn = ttk.Button(actions, text="Экспорт выбранных", command=self.start_export, state="disabled")
        self.export_btn.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Открыть папку экспорта", command=self._open_out).pack(side="left", padx=(8, 0))
        ttk.Separator(actions, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)
        ttk.Label(actions, text="Блоки:").pack(side="left")
        ttk.Button(actions, text="Все", command=lambda: self._set_all_checked(True)).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Ничего", command=lambda: self._set_all_checked(False)).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="Только найденные", command=self._select_found).pack(side="left", padx=(4, 0))
        self.summary_var = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.summary_var).pack(side="right")

        panes = ttk.Panedwindow(root, orient="vertical")
        panes.pack(fill="both", expand=True)

        tree_wrap = ttk.Frame(panes)
        cols = ("chk", "cls", "ident", "ver", "status", "files")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="ECU / деталь")
        self.tree.heading("chk", text="✓", command=self._toggle_all_heading)
        self.tree.heading("cls", text="Класс")
        self.tree.heading("ident", text="ID")
        self.tree.heading("ver", text="Версия")
        self.tree.heading("status", text="Статус")
        self.tree.heading("files", text="Файлы")
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

    def _path_row(self, parent, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label, width=10).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(parent, text="Обзор…", command=command).grid(row=row, column=2, pady=2)
        parent.columnconfigure(1, weight=1)

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
        path = filedialog.askdirectory(title="Папка PSdZData", initialdir=self.psdz_var.get() or os.getcwd())
        if path:
            self.psdz_var.set(path)

    def _browse_svt(self) -> None:
        path = filedialog.askopenfilename(
            title="SVT XML",
            initialdir=str(Path(self.svt_var.get()).parent) if self.svt_var.get() else os.getcwd(),
            filetypes=[("SVT XML", "*.xml"), ("Все файлы", "*.*")],
        )
        if path:
            self.svt_var.set(path)

    def _browse_out(self) -> None:
        path = filedialog.askdirectory(title="Папка экспорта", initialdir=self.out_var.get() or os.getcwd())
        if path:
            self.out_var.set(path)

    def _open_out(self) -> None:
        path = Path(self.out_var.get().strip())
        if not path.exists():
            messagebox.showinfo(APP_TITLE, "Папка экспорта ещё не создана.")
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
            messagebox.showerror(APP_TITLE, "Укажите существующую папку PSdZData.")
            return
        if not svt or not Path(svt).is_file():
            messagebox.showerror(APP_TITLE, "Укажите файл SVT XML.")
            return
        self._persist()
        self._set_busy(True)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Чтение SVT и индекс PSdZData…")
        threading.Thread(target=self._search_worker, args=(psdz, svt), daemon=True).start()

    def _search_worker(self, psdz: str, svt: str) -> None:
        try:
            self.after(0, lambda: self.log_line(f"SVT: {svt}"))
            doc = parse_svt(svt)
            classes = sorted({part.process_class for _, part in doc.parts})
            self.after(0, lambda: self.log_line(f"ECU: {len(doc.ecus)}, деталей: {len(doc.parts)}"))
            swe = resolve_swe_root(psdz)
            self.after(0, lambda: self.log_line(f"SWE: {swe}"))
            index = build_index(
                swe,
                progress=lambda n, kind: self.after(
                    0, lambda n=n, kind=kind: self.status_var.set(f"Индекс {kind}: {n} файлов…")
                ),
            )
            counts = ", ".join(f"{k}={v}" for k, v in sorted(index.kind_counts.items())) or "пусто"
            self.after(0, lambda: self.log_line(f"В индексе {index.file_count} файлов ({counts})"))
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
        stats = summarize(matches)
        self.summary_var.set(
            f"Найдено {stats['found']} / {stats['parts']}  •  файлов {stats['unique_files']}  •  нет {stats['missing']}"
            + (f"  •  другие версии {stats['other']}" if stats["other"] else "")
        )
        self.log_line(self.summary_var.get())
        if stats["found"] == 0:
            self.log_line("Ничего не найдено. Lite-сборки часто содержат только CAFD, без BTLD/SWFL/SWFK.")
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._refresh_selection_status()
        self.status_var.set("Поиск завершён. " + self.status_var.get())
        self._set_busy(False)

    def _search_failed(self, exc: Exception) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Ошибка поиска.")
        self.log_line(traceback.format_exc())
        self._set_busy(False)
        messagebox.showerror(APP_TITLE, str(exc))

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
                values=(MARK_ON, "", "", "", f"{found} найдено", ""),
                tags=("ecu",),
                open=found > 0,
            )
            for part_index, match in enumerate(items):
                if match.found:
                    status = f"найден ({len(match.files)})"
                    files = ", ".join(path.name for path in match.files)
                    tag = "found"
                elif match.other_versions:
                    status = "есть другие версии"
                    files = ", ".join(match.other_versions[:8])
                    tag = "other"
                else:
                    status = "нет в PSdZData"
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
            f"Выбрано блоков: {checked_blocks}/{blocks}  •  деталей: {checked_parts}/{len(self.row_matches)}  •  к экспорту: {ready}"
        )
        if not self._busy:
            self.export_btn.configure(state="normal" if ready else "disabled")

    def start_export(self) -> None:
        if self._busy or not self.matches:
            return
        if not self._selected_classes():
            messagebox.showinfo(APP_TITLE, "Отметьте хотя бы один тип для экспорта.")
            return
        if not any(self.checked.values()):
            messagebox.showinfo(APP_TITLE, "Отметьте блоки галочками: Все, Ничего или конкретные ECU.")
            return
        found = self._selected_matches()
        if not found:
            messagebox.showinfo(APP_TITLE, "Среди отмеченных блоков нет найденных файлов.")
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showerror(APP_TITLE, "Укажите папку экспорта.")
            return
        self._persist()
        self._set_busy(True)
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self.status_var.set("Копирование…")
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
        self.status_var.set(f"Копирование {done}/{total}: {name}")

    def _export_done(self, copied: int, skipped: int, errors: list[str], out: str) -> None:
        self.progress.configure(value=100)
        msg = f"Готово. Скопировано: {copied}, пропущено (уже есть): {skipped}, ошибок: {len(errors)}"
        self.status_var.set(msg)
        self.log_line(msg)
        self.log_line(f"Папка: {out}")
        if errors:
            for line in errors[:20]:
                self.log_line("ERR " + line)
        self._set_busy(False)
        if errors:
            messagebox.showwarning(APP_TITLE, msg)
        else:
            messagebox.showinfo(APP_TITLE, msg + f"\n\n{out}")

    def _persist(self) -> None:
        save_config(
            {
                "psdz": self.psdz_var.get().strip(),
                "svt": self.svt_var.get().strip(),
                "out": self.out_var.get().strip(),
                "layout": self.layout_var.get(),
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
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--svt", help="SVT XML")
    parser.add_argument("--psdz", help="Папка PSdZData")
    parser.add_argument("--out", help="Папка экспорта")
    parser.add_argument("--layout", choices=[item[0] for item in LAYOUTS], default="psdzdata")
    args = parser.parse_args()
    if args.svt and args.psdz and args.out:
        run_cli(args.svt, args.psdz, args.out, args.layout)
        return
    App().mainloop()


if __name__ == "__main__":
    main()
