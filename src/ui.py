# -*- coding: utf-8 -*-
"""
PowerRenamePy tkinter 图形界面。

布局：
  工具栏（文件夹路径 + 加载 + 筛选选项）
  左右分栏：左侧规则面板（规则列表 + 动态参数表单），右侧预览表格
  底部：应用 / 撤销 / 统计
"""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from rename_engine import (
    RULE_LIST,
    RULE_TYPES,
    STATUS_CONFLICT,
    STATUS_ERROR,
    STATUS_LABELS,
    STATUS_OK,
    STATUS_UNCHANGED,
    TYPE_BY_ID,
    ApplyResult,
    RenameRule,
    UndoManager,
    apply_renames,
    compute_preview,
    default_rule,
    load_entries,
    make_rule,
    parse_rename_list,
    rule_summary,
)


def _read_text_auto_encoding(path: Path, fallback: str = "utf-8") -> str:
    """读取文本文件，自动识别编码（优先 UTF-8，失败回退 GBK，再回退指定编码）。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", fallback):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(fallback, errors="replace")


# 预览表格行配色（浅色主题）
_TAG_COLORS = {
    STATUS_OK: {"foreground": "#0a6b2d"},
    STATUS_CONFLICT: {"foreground": "#b00d1c", "background": "#ffe9e9"},
    STATUS_ERROR: {"foreground": "#b00d1c"},
    STATUS_UNCHANGED: {"foreground": "#8a8a8a"},
}


class PowerRenameApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("PowerRename Py — 批量重命名工具")
        root.geometry("1220x760")
        root.minsize(980, 600)

        self.entries = []
        self.preview = []
        self.rules: list[RenameRule] = []
        self.undo_mgr = UndoManager()
        self._debounce_after: str | None = None
        self._type_by_label = {t.label: t.id for t in RULE_TYPES}
        self._rule_form_widgets = []  # (key, variable, kind, options)

        self._build_toolbar()
        self._build_main()
        self._build_bottom()
        self._refresh_rules_tree()
        self.refresh_preview()
        self._set_status("选择文件夹并点击「加载」，然后添加重命名规则")

    # ------------------------------------------------------------ 工具栏
    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(bar, text="文件夹:").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.dir_var, width=48)
        entry.pack(side=tk.LEFT, padx=4)
        entry.bind("<Return>", lambda _e: self.load_files())
        ttk.Button(bar, text="浏览…", command=self._browse).pack(side=tk.LEFT)
        ttk.Button(bar, text="加载", command=self.load_files).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="导入清单…", command=self._import_rename_list).pack(side=tk.LEFT, padx=(6, 0))

        row2 = ttk.Frame(self.root, padding=(10, 2, 10, 4))
        row2.pack(side=tk.TOP, fill=tk.X)

        self.recursive_var = tk.BooleanVar(value=True)
        self.only_files_var = tk.BooleanVar(value=True)
        self.only_dirs_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="包含子文件夹", variable=self.recursive_var,
                        command=self._schedule_reload).pack(side=tk.LEFT)
        ttk.Checkbutton(row2, text="仅文件", variable=self.only_files_var,
                        command=self._schedule_reload).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Checkbutton(row2, text="仅文件夹", variable=self.only_dirs_var,
                        command=self._schedule_reload).pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(row2, text="包含:").pack(side=tk.LEFT, padx=(18, 2))
        self.inc_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.inc_var, width=16).pack(side=tk.LEFT)
        self.inc_var.trace_add("write", lambda *a: self._schedule_reload())

        ttk.Label(row2, text="排除:").pack(side=tk.LEFT, padx=(10, 2))
        self.exc_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.exc_var, width=16).pack(side=tk.LEFT)
        self.exc_var.trace_add("write", lambda *a: self._schedule_reload())

        self.regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="正则匹配", variable=self.regex_var,
                        command=self._schedule_reload).pack(side=tk.LEFT, padx=(10, 0))

    def _schedule_reload(self, *_a) -> None:
        if self._debounce_after is not None:
            self.root.after_cancel(self._debounce_after)
        self._debounce_after = self.root.after(250, self.load_files)

    # ------------------------------------------------------------ 主分栏
    def _build_main(self) -> None:
        paned = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(4, 6))

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=2)
        paned.add(right, weight=5)
        self._build_rule_panel(left)
        self._build_preview_panel(right)

    # ------------------------------------------------------------ 规则面板
    def _build_rule_panel(self, parent) -> None:
        lf = ttk.LabelFrame(parent, text="重命名规则（按顺序依次应用）", padding=6)
        lf.pack(fill=tk.BOTH, expand=True)

        tree = ttk.Treeview(lf, columns=("idx", "type", "summary"),
                            show="headings", height=7)
        tree.heading("idx", text="#")
        tree.heading("type", text="类型")
        tree.heading("summary", text="规则内容")
        tree.column("idx", width=36, stretch=False, anchor=tk.CENTER)
        tree.column("type", width=92, stretch=False, anchor=tk.CENTER)
        tree.column("summary", width=230)
        vsb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tree.bind("<<TreeviewSelect>>", self._on_rule_select)
        self.rule_tree = tree
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

        btns = ttk.Frame(lf)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.rule_type_var = tk.StringVar(value=RULE_TYPES[0].label)
        cb = ttk.Combobox(btns, textvariable=self.rule_type_var, state="readonly",
                          width=14, values=[t.label for t in RULE_TYPES])
        cb.pack(side=tk.LEFT)
        ttk.Button(btns, text="添加", command=self._add_rule).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="删除", command=self._del_rule).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(btns, text="↑", width=3, command=lambda: self._move_rule(-1)).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(btns, text="↓", width=3, command=lambda: self._move_rule(1)).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Button(btns, text="清空", command=self._clear_rules).pack(side=tk.LEFT, padx=(2, 0))

        form = ttk.LabelFrame(lf, text="规则参数", padding=8)
        form.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.rule_form = form
        self._build_rule_form()

    def _selected_rule_index(self) -> int | None:
        sel = self.rule_tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def _selected_rule(self) -> RenameRule | None:
        idx = self._selected_rule_index()
        if idx is None or not (0 <= idx < len(self.rules)):
            return None
        return self.rules[idx]

    def _on_rule_select(self, _event=None) -> None:
        self._build_rule_form()

    # ---------------------------------------------------- 规则参数动态表单
    def _build_rule_form(self) -> None:
        for w in self.rule_form.winfo_children():
            w.destroy()
        self._rule_form_widgets = []

        rule = self._selected_rule()
        if rule is None:
            ttk.Label(self.rule_form, text="在左侧列表添加或选中一条规则后，可在此编辑参数。",
                      foreground="#777").pack(anchor="w")
            return

        tdef = TYPE_BY_ID[rule.rule_type]
        ttk.Label(self.rule_form, text=f"类型：{tdef.label}",
                  font=("", 10, "bold")).grid(row=0, column=0, columnspan=2,
                                              sticky="w", pady=(0, 4))

        row = 1
        for f in tdef.fields:
            ttk.Label(self.rule_form, text=f.label + ":").grid(
                row=row, column=0, sticky="e", padx=(0, 6), pady=2)
            val = rule.params.get(f.key, f.default)

            if f.kind == "map":
                # 清单规则：映射由「导入清单」设置，此处只展示条数与查看入口
                mapping = val if isinstance(val, dict) else {}
                info = ttk.Frame(self.rule_form)
                info.grid(row=row, column=1, sticky="w")
                ttk.Label(info, text=f"已导入 {len(mapping)} 项映射").pack(side=tk.LEFT)
                ttk.Button(info, text="查看映射…", width=10,
                           command=self._show_list_mapping).pack(side=tk.LEFT, padx=(6, 0))
            elif f.kind == "bool":
                var = tk.BooleanVar(value=bool(val))
                widget = ttk.Checkbutton(self.rule_form, variable=var)
                widget.grid(row=row, column=1, sticky="w")
                self._rule_form_widgets.append((f.key, var, f.kind, None))
            elif f.kind == "choice":
                label_of = {v: lbl for v, lbl in f.options}
                var = tk.StringVar(value=label_of.get(val, ""))
                widget = ttk.Combobox(self.rule_form, textvariable=var, state="readonly",
                                      width=24, values=[lbl for _, lbl in f.options])
                widget.grid(row=row, column=1, sticky="w")
                self._rule_form_widgets.append((f.key, var, f.kind, f.options))
            else:
                var = tk.StringVar(value=str(val))
                widget = ttk.Entry(self.rule_form, textvariable=var, width=26)
                widget.grid(row=row, column=1, sticky="w", pady=2)
                self._rule_form_widgets.append((f.key, var, f.kind, None))
            row += 1

        ttk.Button(self.rule_form, text="保存修改",
                   command=self._save_rule_form).grid(row=row, column=0, columnspan=2,
                                                      pady=(8, 0))

    def _save_rule_form(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        for key, var, kind, options in self._rule_form_widgets:
            if kind == "bool":
                rule.params[key] = bool(var.get())
            elif kind == "choice":
                label = var.get()
                for v, lbl in options:
                    if lbl == label:
                        rule.params[key] = v
                        break
            elif kind == "int":
                try:
                    rule.params[key] = int(var.get())
                except ValueError:
                    pass  # 非法输入保持原值
            else:
                rule.params[key] = var.get()
        self._refresh_rules_tree()
        self.refresh_preview()
        self._set_status(f"规则 {self._selected_rule_index() + 1} 已更新")

    # ---------------------------------------------------- 规则增删改
    def _add_rule(self) -> None:
        rule_type = self._type_by_label.get(self.rule_type_var.get(), RULE_TYPES[0].id)
        rule = default_rule(rule_type)
        self.rules.append(rule)
        self._refresh_rules_tree(select_index=len(self.rules) - 1)
        self._build_rule_form()
        self.refresh_preview()
        self._set_status(f"已添加规则：{TYPE_BY_ID[rule_type].label}")

    def _del_rule(self) -> None:
        idx = self._selected_rule_index()
        if idx is None:
            return
        del self.rules[idx]
        self._refresh_rules_tree(select_index=min(idx, len(self.rules) - 1))
        self._build_rule_form()
        self.refresh_preview()

    def _move_rule(self, delta: int) -> None:
        idx = self._selected_rule_index()
        if idx is None:
            return
        new_idx = idx + delta
        if not (0 <= new_idx < len(self.rules)):
            return
        self.rules[idx], self.rules[new_idx] = self.rules[new_idx], self.rules[idx]
        self._refresh_rules_tree(select_index=new_idx)
        self.refresh_preview()

    def _clear_rules(self) -> None:
        if not self.rules:
            return
        if messagebox.askyesno("清空规则", "确定删除全部规则？"):
            self.rules.clear()
            self._refresh_rules_tree()
            self._build_rule_form()
            self.refresh_preview()

    def _refresh_rules_tree(self, select_index: int | None = None) -> None:
        tree = self.rule_tree
        tree.delete(*tree.get_children())
        for i, rule in enumerate(self.rules):
            tdef = TYPE_BY_ID[rule.rule_type]
            tree.insert("", "end", iid=str(i),
                        values=(i + 1, tdef.label, rule_summary(rule)))
        if select_index is not None and self.rules:
            iid = str(select_index)
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)

    # ------------------------------------------------------------ 预览面板
    def _build_preview_panel(self, parent) -> None:
        lf = ttk.LabelFrame(parent, text="重命名预览", padding=6)
        lf.pack(fill=tk.BOTH, expand=True)

        tree = ttk.Treeview(lf, columns=("old", "new", "status", "note"), show="headings")
        tree.heading("old", text="当前名称")
        tree.heading("new", text="新名称")
        tree.heading("status", text="状态")
        tree.heading("note", text="说明")
        tree.column("old", width=280)
        tree.column("new", width=280)
        tree.column("status", width=64, anchor=tk.CENTER, stretch=False)
        tree.column("note", width=220)

        vsb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=tree.yview)
        hsb = ttk.Scrollbar(lf, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

        for status, cfg in _TAG_COLORS.items():
            tree.tag_configure(status, **cfg)

        tree.bind("<Button-3>", self._on_preview_rmb)
        self.preview_tree = tree

    def refresh_preview(self) -> None:
        self.preview = compute_preview(self.entries, self.rules) if self.entries else []
        tree = self.preview_tree
        tree.delete(*tree.get_children())
        for i, item in enumerate(self.preview):
            tree.insert("", "end", iid=str(i),
                        values=(item.old_name, item.new_name,
                                STATUS_LABELS[item.status], item.note),
                        tags=(item.status,))

        n_ok = sum(1 for it in self.preview if it.status == STATUS_OK)
        n_conf = sum(1 for it in self.preview if it.status == STATUS_CONFLICT)
        n_err = sum(1 for it in self.preview if it.status == STATUS_ERROR)
        n_unch = sum(1 for it in self.preview if it.status == STATUS_UNCHANGED)
        self.stats_var.set(
            f"共 {len(self.preview)} 项  |  将重命名 {n_ok}  |  冲突 {n_conf}  |  "
            f"错误 {n_err}  |  无变化 {n_unch}")

    def _on_preview_rmb(self, event) -> None:
        iid = self.preview_tree.identify_row(event.y)
        if not iid:
            return
        self.preview_tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="打开所在文件夹", command=lambda: self._reveal(int(iid)))
        menu.add_command(label="刷新预览", command=self.refresh_preview)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _reveal(self, idx: int) -> None:
        if not (0 <= idx < len(self.preview)):
            return
        path = self.preview[idx].entry.path
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    # ------------------------------------------------------------ 底部操作栏
    def _build_bottom(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 6))
        bar.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Button(bar, text="应用重命名", command=self._apply).pack(side=tk.LEFT)
        self.undo_btn = ttk.Button(bar, text="撤销上次", command=self._undo, state=tk.DISABLED)
        self.undo_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.stats_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.stats_var).pack(side=tk.LEFT, padx=(18, 0))

        self.status_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.RIGHT)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _refresh_undo_btn(self) -> None:
        self.undo_btn.config(state=tk.NORMAL if self.undo_mgr.can_undo else tk.DISABLED)

    # ------------------------------------------------------------ 业务操作
    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or str(Path.home()))
        if d:
            self.dir_var.set(d)
            self.load_files()

    # ------------------------------------------------------------ 清单重命名
    def _import_rename_list(self) -> None:
        """选择清单文件（txt/csv）→ 解析为 {原名: 新名} 映射 → 设置/替换清单规则。"""
        raw_path = filedialog.askopenfilename(
            title="选择重命名清单文件",
            filetypes=[("文本/CSV", "*.txt *.csv"), ("所有文件", "*.*")],
            initialdir=self.dir_var.get() or str(Path.home()),
        )
        if not raw_path:
            return
        path = Path(raw_path)
        try:
            text = _read_text_auto_encoding(path)
        except OSError as exc:
            messagebox.showerror("导入清单", f"读取文件失败：\n{exc}")
            return

        mapping = parse_rename_list(text)
        if not mapping:
            messagebox.showwarning(
                "导入清单",
                "未解析到有效的「原名→新名」条目。\n\n"
                "支持每行一条，分隔符：→ / -> / => / Tab / 逗号 / 分号 / 竖线 / 连续空格。\n"
                "例如：\n  photo1.jpg → wedding1.jpg",
            )
            return

        # 找到已有清单规则则替换其映射，否则追加一条
        list_rules = [r for r in self.rules if r.rule_type == RULE_LIST]
        if list_rules:
            list_rules[-1].params["mapping"] = mapping
        else:
            self.rules.append(make_rule(RULE_LIST, mapping=mapping))

        self._refresh_rules_tree(select_index=len(self.rules) - 1)
        self._build_rule_form()
        self.refresh_preview()
        self._set_status(
            f"已导入清单：{len(mapping)} 条映射（{path.name}）；"
            "未匹配清单的文件将保持原名")

    def _show_list_mapping(self) -> None:
        """弹窗查看当前清单规则的映射明细。"""
        rule = self._selected_rule()
        if rule is None or rule.rule_type != RULE_LIST:
            return
        mapping = rule.params.get("mapping", {})
        win = tk.Toplevel(self.root)
        win.title("清单映射")
        win.geometry("520x420")
        win.transient(self.root)

        frame = ttk.Frame(win, padding=8)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=f"共 {len(mapping)} 条映射").pack(anchor="w")
        text = tk.Text(frame, wrap=tk.NONE)
        vsb = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        hsb = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        text.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        hsb.grid(row=2, column=0, sticky="ew")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        for old, new in mapping.items():
            text.insert(tk.END, f"{old}  →  {new}\n")
        text.config(state=tk.DISABLED)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=6)

    def load_files(self, *_a) -> None:
        raw = self.dir_var.get().strip()
        if not raw:
            return
        p = Path(raw)
        if not p.is_dir():
            self._set_status("无效的文件夹路径")
            return
        self.entries = load_entries(
            p,
            recursive=self.recursive_var.get(),
            include_files=self.only_files_var.get(),
            include_dirs=self.only_dirs_var.get(),
            inc_text=self.inc_var.get().strip(),
            exc_text=self.exc_var.get().strip(),
            use_regex=self.regex_var.get(),
        )
        self.refresh_preview()
        self._set_status(f"已加载 {len(self.entries)} 项，来自 {p}")

    def _apply(self) -> None:
        targets = [it for it in self.preview if it.status == STATUS_OK]
        if not targets:
            messagebox.showinfo("应用重命名", "没有可执行的重命名（就绪项为 0）。\n"
                                            "请检查冲突/错误条目或修改规则。")
            return

        msg = (f"将重命名 {len(targets)} 项，确认执行？\n\n"
               f"（冲突 {sum(1 for it in self.preview if it.status == STATUS_CONFLICT)} 项、"
               f"错误 {sum(1 for it in self.preview if it.status == STATUS_ERROR)} 项将被跳过）")
        if not messagebox.askyesno("应用重命名", msg):
            return

        items = [(it.entry.path, it.entry.path.with_name(it.new_name)) for it in targets]
        result: ApplyResult = apply_renames(items)

        if result.rolled_back:
            messagebox.showerror("重命名失败",
                                 "重命名过程中出现错误，已自动回滚全部改动：\n\n" +
                                 "\n".join(result.errors[:8]))
        else:
            if result.errors:
                messagebox.showwarning("部分失败", "\n".join(result.errors[:8]))
            self.undo_mgr.push(result.logs)
            if result.logs:
                self._set_status(f"已重命名 {len(result.logs)} 项")

        self._refresh_undo_btn()
        self.load_files()

    def _undo(self) -> None:
        done, errors = self.undo_mgr.undo()
        self._refresh_undo_btn()
        if errors:
            messagebox.showwarning("撤销",
                                   f"成功恢复 {done} 项；以下项失败：\n\n" +
                                   "\n".join(errors[:8]))
        else:
            self._set_status(f"已撤销，恢复 {done} 项")
        self.load_files()
