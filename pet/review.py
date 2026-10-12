"""学习回顾复习弹窗（P3）：到期卡片 → 回忆 → 显示解释 → SM-2 打分。

流程：先展示概念让主人回忆，点「显示解释」看答案，再打分（忘记/困难/记得/简单），
后端按 SM-2 更新复习间隔。置顶但**不用 transient**（主 root withdraw 时不可见）。
视觉 token 引用 theme.py。
"""
from __future__ import annotations

import threading
import tkinter as tk

from . import theme as T
from . import ui
from .api_client import ApiClient

# 打分按钮配色（由浅到深反馈：忘记/困难/记得/简单，随主题动态取）
def _grade_color(grade: str) -> str:
    return {"again": T.GRADE_AGAIN, "hard": T.GRADE_HARD,
            "good": T.GRADE_GOOD, "easy": T.GRADE_EASY}.get(grade, T.ERR)


class ReviewDialog:
    def __init__(self, root: tk.Tk, api: ApiClient) -> None:
        self.root = root
        self.api = api
        self._cards: list[dict] = []
        self._idx = 0
        self._revealed = False

        self.win = tk.Toplevel(root)
        self.win.title("学习回顾复习")
        self.win.geometry("520x460")
        self.win.minsize(440, 400)
        self.win.configure(bg=T.BG)
        self.win.attributes("-topmost", True)
        T.apply_window_material(self.win, acrylic=True)
        self._build()
        self._load()

    def _build(self) -> None:
        self._progress = tk.Label(self.win, text="加载中…", bg=T.BG, fg=T.INK2,
                                  font=T.F_SMALL)
        self._progress.pack(fill="x", padx=18, pady=(14, 4))

        self._concept = tk.Label(self.win, text="", bg=T.CARD, fg=T.INK, wraplength=440,
                                 font=(T.F_SANS, 15, "bold"), justify="left",
                                 padx=14, pady=12, anchor="w", highlightthickness=1,
                                 highlightbackground=T.BORDER)
        self._concept.pack(fill="x", padx=18, pady=6)

        self._explanation = tk.Label(self.win, text="", bg=T.BG, fg=T.INK2, wraplength=440,
                                     font=T.F_BODY, justify="left", anchor="w")
        self._explanation.pack(fill="both", expand=True, padx=18, pady=6)

        self._msg = tk.Label(self.win, text="", bg=T.BG, fg=T.INK2, font=T.F_SMALL)
        self._msg.pack(fill="x", padx=18)

        btn_row = tk.Frame(self.win, bg=T.BG)
        btn_row.pack(fill="x", padx=18, pady=(4, 14))
        self._reveal_btn = ui.primary_button(btn_row, "显示解释", self._reveal,
                                             icon=T.ICON["eye"],
                                             padx=16, pady=6,
                                             font=(T.F_SANS, 10, "bold"))
        self._reveal_btn.pack(side="left")

        self._grade_btns: dict[str, tk.Button] = {}
        for grade, label in (("again", "忘记"), ("hard", "困难"),
                             ("good", "记得"), ("easy", "简单")):
            color = _grade_color(grade)
            btn = tk.Button(btn_row, text=label, command=lambda g=grade: self._grade(g),
                            bg=color, fg=T.ON_ACCENT, relief="flat", padx=12, pady=6,
                            font=(T.F_SANS, 9, "bold"), cursor="hand2",
                            activebackground=T.CARD_HOVER, bd=0)
            btn.pack(side="right", padx=(4, 0))
            btn.configure(state="disabled")
            self._grade_btns[grade] = btn

    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._run(self.api.learning_due, self._load_done)

    def _load_done(self, result, err) -> None:
        if err:
            self._progress.configure(text=f"加载失败：{err}", fg=T.ERR)
            return
        self._cards = (result or {}).get("items") or []
        if not self._cards:
            self._concept.configure(text="暂无到期的复习卡片")
            self._explanation.configure(text="主人可以去柴犬菜单「学习回顾」提取新卡片，或稍后再来。")
            self._reveal_btn.configure(state="disabled")
            return
        self._idx = 0
        self._show()

    def _show(self) -> None:
        card = self._cards[self._idx]
        self._revealed = False
        self._progress.configure(text=f"第 {self._idx + 1} / {len(self._cards)} 张 · "
                                      f"复习 {card.get('reps', 0)} 次 · 间隔 {card.get('interval_days', 0)} 天",
                                 fg=T.INK2)
        self._concept.configure(text=card.get("concept", ""))
        self._explanation.configure(text="🤔 先回忆一下这是什么，然后点「显示解释」")
        self._reveal_btn.configure(state="normal")
        for btn in self._grade_btns.values():
            btn.configure(state="disabled")
        self._msg.configure(text="")

    def _reveal(self) -> None:
        if self._idx >= len(self._cards):
            return
        card = self._cards[self._idx]
        self._explanation.configure(text=card.get("explanation", ""))
        self._revealed = True
        self._reveal_btn.configure(state="disabled")
        for btn in self._grade_btns.values():
            btn.configure(state="normal")

    def _grade(self, grade: str) -> None:
        if not self._revealed or self._idx >= len(self._cards):
            return
        card = self._cards[self._idx]
        self._run(lambda: self.api.learning_review(card["id"], grade), self._grade_done)

    def _grade_done(self, result, err) -> None:
        if err:
            self._msg.configure(text=f"打分失败：{err}", fg=T.ERR)
            return
        r = result or {}
        self._msg.configure(
            text=f"已记录（{r.get('grade_label', '')}）· 下次复习间隔 {r.get('interval_days', 0)} 天",
            fg=T.OK)
        self._idx += 1
        if self._idx < len(self._cards):
            self._show()
        else:
            self._progress.configure(text=f"完成！本次复习 {len(self._cards)} 张卡片 🎉", fg=T.INK2)
            self._concept.configure(text="全部复习完毕")
            self._explanation.configure(text="主人可以去菜单「学习回顾」提取新卡片，或点击「刷新」再来一轮。")
            self._reveal_btn.configure(state="disabled")
            for btn in self._grade_btns.values():
                btn.configure(state="disabled")

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
