"""柴犬聊天弹窗（P2）：QQ 风格聊天气泡 + 输入框 + 发送。

- 走后端 /api/pet/chat（人设 + 最近事件上下文 + 对话历史）；
- 消息区为滚动气泡（柴犬=米橙泡，主人=天蓝泡）；
- 置顶但**不用 transient**（主 root withdraw 时 transient 子窗口不可见，见 dialogs.py 注）；
- 视觉 token 引用 theme.py。
"""
from __future__ import annotations

import threading
import tkinter as tk

from . import theme as T
from . import ui
from .api_client import ApiClient

PET = "#e8b87f"


class ChatDialog:
    """与柴犬「阿柴」聊天（气泡消息）。"""

    def __init__(self, root: tk.Tk, api: ApiClient) -> None:
        self.root = root
        self.api = api
        self._history: list[dict] = []
        self.win = tk.Toplevel(root)
        self.win.title("和阿柴聊天")
        self.win.geometry("460x560")
        self.win.minsize(380, 440)
        self.win.configure(bg=T.BG)
        self.win.attributes("-topmost", True)
        T.apply_window_material(self.win, acrylic=True)
        self._build()
        self._append("pet", "汪！我是阿柴，主人想聊什么？我能记住你最近在做什么哦～")

    def _build(self) -> None:
        # 滚动消息区（气泡列表）
        wrap = tk.Frame(self.win, bg=T.BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=(12, 6))
        self._scroll = tk.Canvas(wrap, bg=T.BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self._scroll.yview,
                          bg=T.CARD, troughcolor=T.BG, highlightthickness=0, bd=0)
        self._msg_box = tk.Frame(self._scroll, bg=T.BG)
        self._win_id = self._scroll.create_window((0, 0), window=self._msg_box, anchor="nw")
        self._scroll.configure(yscrollcommand=sb.set)
        self._msg_box.bind("<Configure>", self._on_box_configure)
        self._scroll.bind("<Configure>", self._on_scroll_configure)
        sb.pack(side="right", fill="y")
        self._scroll.pack(side="left", fill="both", expand=True)

        # 输入行
        row = tk.Frame(self.win, bg=T.BG)
        row.pack(fill="x", padx=10, pady=(0, 12))
        self._entry = tk.Entry(row, bg=T.CARD, fg=T.INK, insertbackground=T.INK,
                               relief="flat", font=T.F_BODY, highlightthickness=1,
                               highlightbackground=T.BORDER, highlightcolor=T.ACCENT)
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._entry.bind("<Return>", lambda _e: self._send())
        self._entry.focus_set()
        ui.primary_button(row, "发送", self._send, icon=T.ICON["send"],
                          padx=14, pady=5, font=T.F_BODY).pack(side="left")

    # ------------------------------------------------------------------
    def _on_box_configure(self, _e: tk.Event) -> None:
        self._scroll.configure(scrollregion=self._scroll.bbox("all"))

    def _on_scroll_configure(self, e: tk.Event) -> None:
        self._scroll.itemconfigure(self._win_id, width=e.width)

    def _append(self, who: str, text: str) -> None:
        maxw = self._msg_box.winfo_width() or 300
        bubble = ui.Bubble(self._msg_box, text, who, maxw=maxw - 24)
        bubble.pack(anchor="e" if who == "user" else "w", pady=3, padx=2)
        # 布局完成后滚动到底
        try:
            self.win.after(10, lambda: self._scroll.yview_moveto(1.0))
        except tk.TclError:
            pass

    def _send(self) -> None:
        msg = self._entry.get().strip()
        if not msg:
            return
        self._entry.delete(0, "end")
        self._append("user", msg)
        self._history.append({"role": "user", "content": msg})
        self._entry.configure(state="disabled")
        self._run(lambda: self.api.pet_chat(msg, self._history), self._done)

    def _done(self, result, err) -> None:
        self._entry.configure(state="normal")
        self._entry.focus_set()
        if err:
            self._append("pet", f"汪…刚才卡住了：{err}")
            return
        reply = (result or {}).get("reply", "")
        self._append("pet", reply)
        self._history.append({"role": "assistant", "content": reply})

    def _run(self, fn, on_done) -> None:
        def work() -> None:
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                self.win.after(0, lambda e=exc: on_done(None, e))
            else:
                self.win.after(0, lambda r=result: on_done(r, None))
        threading.Thread(target=work, daemon=True).start()
