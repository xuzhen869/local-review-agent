"""待办清单弹窗（P2）：列表 + 添加 + 勾选 + 删除 + 到期时间。

- 置顶但**不用 transient**（主 root withdraw 时 transient 子窗口不可见）；
- 到期输入支持两种：`YYYY-MM-DD HH:MM` 或 ISO 串（`2026-08-06T18:00`），留空表示无期限；
- 视觉 token 引用 theme.py。
"""
from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import theme as T
from . import ui
from .api_client import ApiClient

_PRIO_COLOR = {"高": T.ERR, "中": T.WARN, "低": T.OK}


def _parse_due(raw: str) -> str | None:
    """解析到期时间；无法解析返回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    if "T" in s:
        try:
            datetime.fromisoformat(s)
            return s
        except ValueError:
            return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
        return dt.astimezone().isoformat(timespec="seconds")
    except ValueError:
        return None


class TodoDialog:
    def __init__(self, root: tk.Tk, api: ApiClient) -> None:
        self.root = root
        self.api = api
        self.win = tk.Toplevel(root)
        self.win.title("待办清单")
        self.win.geometry("600x440")
        self.win.minsize(500, 380)
        self.win.configure(bg=T.BG)
        self.win.attributes("-topmost", True)
        T.apply_window_material(self.win, acrylic=True)
        self._build()
        self._load()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        cols = ("done", "prio", "text", "due")
        style = ttk.Style()
        style.configure("Todo.Treeview", background=T.CARD, foreground=T.INK,
                        fieldbackground=T.CARD, borderwidth=0, rowheight=26)
        style.configure("Todo.Treeview.Heading", background=T.SURFACE,
                        foreground=T.INK2, relief="flat", font=(T.F_SANS, 9, "bold"))
        style.map("Todo.Treeview", background=[("selected", T.CARD_ACTIVE)])
        self._tree = ttk.Treeview(self.win, columns=cols, show="headings",
                                  height=12, style="Todo.Treeview")
        for c, h, w in zip(cols, ("✓", "优先级", "内容", "到期"),
                           (44, 64, 340, 140)):
            self._tree.heading(c, text=h)
            self._tree.column(c, width=w, anchor="center" if c in ("done", "prio") else "w")
        self._tree.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self._tree.tag_configure("done", foreground=T.MUTED)
        self._tree.bind("<Double-1>", lambda _e: self._toggle())

        # 输入行
        row = tk.Frame(self.win, bg=T.BG)
        row.pack(fill="x", padx=12, pady=(0, 6))
        self._entry = tk.Entry(row, bg=T.CARD, fg=T.INK, insertbackground=T.INK,
                               relief="flat", font=T.F_BODY, highlightthickness=1,
                               highlightbackground=T.BORDER, highlightcolor=T.ACCENT)
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._entry.bind("<Return>", lambda _e: self._add())
        self._prio_var = tk.StringVar(value="中")
        tk.OptionMenu(row, self._prio_var, "高", "中", "低").configure(
            bg=T.CARD, fg=T.INK, activebackground=T.CARD_HOVER, activeforeground=T.INK,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=T.BORDER)
        self._prio_menu = row.winfo_children()[-1]
        self._due = tk.Entry(row, width=18, bg=T.CARD, fg=T.INK, insertbackground=T.INK,
                             relief="flat", font=T.F_SMALL, highlightthickness=1,
                             highlightbackground=T.BORDER, highlightcolor=T.ACCENT)
        self._due.insert(0, "")
        self._due.pack(side="left", padx=(6, 0))

        btns = tk.Frame(self.win, bg=T.BG)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        for text, icon, cmd, primary in (
                ("添加", T.ICON["add"], self._add, True),
                ("勾选", T.ICON["check"], self._toggle, False),
                ("删除", T.ICON["delete"], self._delete, False),
                ("刷新", T.ICON["refresh"], self._load, False)):
            btn = (ui.primary_button(btns, text, cmd, icon=icon,
                                     padx=12, pady=4, font=T.F_SMALL)
                   if primary else
                   ui.ghost_button(btns, text, cmd, icon=icon,
                                   padx=12, pady=4, font=T.F_SMALL))
            btn.pack(side="left", padx=(0, 8))
        self._msg = ui.message(self.win, "到期格式：2026-08-06 18:00（可空）",
                               font=T.F_TINY)
        self._msg.pack(fill="x", padx=12, pady=(0, 8))

    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._run(self.api.todos_list, self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._msg.configure(text=f"加载失败：{err}", fg=T.ERR)
            return
        self._tree.delete(*self._tree.get_children())
        items = (result or {}).get("items") or []
        self._cached_items = items  # 供按文本反查 id（Treeview 不直接携带 id）
        for it in items:
            done = "✓" if it.get("done") else ""
            due = (it.get("due_at") or "")[:16].replace("T", " ")
            self._tree.insert("", "end",
                              values=(done, it.get("priority"), it.get("text"), due),
                              tags=("done",) if it.get("done") else ())
        self._msg.configure(text=f"共 {len(items)} 条", fg=T.INK2)

    def _add(self) -> None:
        text = self._entry.get().strip()
        if not text:
            self._msg.configure(text="内容不能为空", fg=T.ERR)
            return
        due = _parse_due(self._due.get())
        if self._due.get().strip() and due is None:
            self._msg.configure(text="到期格式不正确（示例：2026-08-06 18:00）", fg=T.ERR)
            return
        self._run(lambda: self.api.todo_create(text, self._prio_var.get(), due),
                  lambda _r, err: self._after_add(err))
        self._entry.delete(0, "end")

    def _after_add(self, err) -> None:
        if err:
            self._msg.configure(text=f"添加失败：{err}", fg=T.ERR)
        self._load()

    def _toggle(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        item = self._tree.item(sel[0])
        values = item["values"]
        todo_id = self._find_id(values[2])
        if not todo_id:
            return
        new_done = values[0] != "✓"
        self._run(lambda: self.api.todo_update(todo_id, done=new_done),
                  lambda _r, err: self._load())

    def _delete(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0])["values"]
        todo_id = self._find_id(values[2])
        if not todo_id:
            return
        self._run(lambda: self.api.todo_delete(todo_id), lambda _r, err: self._load())

    def _find_id(self, text: str) -> str | None:
        """按文本查找 id（当前展示不直接含 id，从后端列表缓存找）。"""
        cached = getattr(self, "_cached_items", None)
        if cached is None:
            return None
        for it in cached:
            if it.get("text") == text:
                return it.get("id")
        return None

    # ------------------------------------------------------------------
    def _run(self, fn, on_done) -> None:
        def work() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self.win.after(0, lambda e=exc: on_done(None, e))
            else:
                self.win.after(0, lambda r=result: on_done(r, None))
        threading.Thread(target=work, daemon=True).start()
