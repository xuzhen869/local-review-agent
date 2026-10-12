"""宠物功能弹窗（tkinter 版）：知识点录入、今日小结、DeepSeek Key 录入。

视觉 token 统一引用 theme.py，按钮走 ui 工厂函数。
"""
from __future__ import annotations

import tkinter as tk

from . import theme as T
from . import ui


class KnowledgeDialog:
    """知识点录入：预填剪贴板，可编辑。run() 返回文本或 None。"""

    def __init__(self, root: tk.Tk, initial_text: str) -> None:
        self.result_text: str | None = None
        self.win = tk.Toplevel(root)
        self.win.title("记录知识点")
        self.win.geometry("560x380")
        self.win.configure(bg=T.BG)
        self.win.attributes("-topmost", True)
        T.apply_window_material(self.win, acrylic=True)
        # 注：不能 transient(root) —— 主 root 处于 withdraw 隐藏态时，
        # Windows 下 transient 子窗口不可见（mapped=0）。直接独立显示。

        tk.Label(self.win, text="粘贴或编辑要存入知识库的内容：",
                 bg=T.BG, fg=T.INK2, anchor="w", font=T.F_BODY).pack(fill="x", padx=14, pady=(12, 4))

        self.text = tk.Text(self.win, bg=T.CARD, fg=T.INK, insertbackground=T.INK,
                            relief="flat", wrap="word", font=T.F_BODY, padx=8, pady=8,
                            highlightthickness=1, highlightbackground=T.BORDER,
                            highlightcolor=T.ACCENT)
        self.text.pack(fill="both", expand=True, padx=14)
        if initial_text:
            self.text.insert("1.0", initial_text)

        btns = tk.Frame(self.win, bg=T.BG)
        btns.pack(fill="x", padx=14, pady=10)
        ui.primary_button(btns, "存入知识库", self._save).pack(side="right")
        ui.ghost_button(btns, "取消", self.win.destroy).pack(side="right", padx=(0, 8))

        self.win.grab_set()

    def _save(self) -> None:
        self.result_text = self.text.get("1.0", "end").strip()
        self.win.destroy()


class KeyDialog:
    """DeepSeek API Key 录入。run() 返回 key 或 None。"""

    def __init__(self, root: tk.Tk, title: str = "录入 DeepSeek API Key") -> None:
        self.result_key: str | None = None
        self.win = tk.Toplevel(root)
        self.win.title(title)
        self.win.geometry("480x140")
        self.win.configure(bg=T.BG)
        self.win.attributes("-topmost", True)
        T.apply_window_material(self.win, acrylic=True)
        # 注：不能 transient(root) —— 主 root 处于 withdraw 隐藏态时，
        # Windows 下 transient 子窗口不可见（mapped=0）。直接独立显示。

        tk.Label(self.win, text="DeepSeek API Key（加密存储，不明文落盘）：",
                 bg=T.BG, fg=T.INK2, anchor="w", font=T.F_BODY).pack(fill="x", padx=14, pady=(14, 4))

        self.edit = tk.Entry(self.win, bg=T.CARD, fg=T.INK, insertbackground=T.INK,
                             show="•", relief="flat", font=T.F_BODY,
                             highlightthickness=1, highlightbackground=T.BORDER,
                             highlightcolor=T.ACCENT)
        self.edit.pack(fill="x", padx=14)
        self.edit.focus_set()

        btns = tk.Frame(self.win, bg=T.BG)
        btns.pack(fill="x", padx=14, pady=12)
        ui.primary_button(btns, "保存", self._save).pack(side="right")
        ui.ghost_button(btns, "取消", self.win.destroy).pack(side="right", padx=(0, 8))

        self.win.bind("<Return>", lambda _e: self._save())
        self.win.grab_set()

    def _save(self) -> None:
        self.result_key = self.edit.get().strip()
        self.win.destroy()


class SummaryDialog:
    """今日小结展示（只读）。含 Markdown 样式渲染（简单 # 标题加粗）。"""

    def __init__(self, root: tk.Tk, summary: str | None, *,
                 error: str | None = None) -> None:
        self.win = tk.Toplevel(root)
        self.win.title("今日周报")
        self.win.geometry("640x520")
        self.win.configure(bg=T.BG)
        self.win.attributes("-topmost", True)
        T.apply_window_material(self.win, acrylic=True)
        # 注：不能 transient(root) —— 主 root 处于 withdraw 隐藏态时，
        # Windows 下 transient 子窗口不可见（mapped=0）。直接独立显示。

        self.text = tk.Text(self.win, bg=T.CARD, fg=T.INK, relief="flat",
                            wrap="word", font=T.F_BODY, padx=16, pady=12,
                            spacing1=4, spacing3=4, highlightthickness=1,
                            highlightbackground=T.BORDER)
        self.text.pack(fill="both", expand=True, padx=14, pady=(14, 0))
        self.text.configure(state="disabled")

        if error:
            self._append(f"生成失败：\n\n{error}\n\n（检查 DeepSeek Key 是否有效）\n",
                         color=T.ERR)
        elif summary:
            self._render_markdown(summary)
        else:
            self._append("（无内容）")

        btns = tk.Frame(self.win, bg=T.BG)
        btns.pack(fill="x", padx=14, pady=10)
        ui.ghost_button(btns, "关闭", self.win.destroy).pack(side="right")
        self.win.grab_set()

    def _render_markdown(self, markdown: str) -> None:
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                self._append("\n" + stripped[4:] + "\n", bold=True, size=13, color=T.ACCENT)
            elif stripped.startswith("## "):
                self._append("\n" + stripped[3:] + "\n", bold=True, size=14, color=T.ACCENT)
            elif stripped.startswith("- "):
                self._append("  • " + stripped[2:] + "\n")
            elif stripped.startswith("# "):
                self._append("\n" + stripped[2:] + "\n", bold=True, size=15)
            else:
                self._append(line + "\n")

    def _append(self, text: str, *, bold: bool = False, size: int = 11,
                color: str = T.INK) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", text, (("bold" if bold else ""),))
        self.text.tag_config("bold", font=(T.F_SANS, size, "bold"))
        self.text.configure(state="disabled")


def info(root: tk.Tk, title: str, message: str) -> None:
    import tkinter.messagebox as mb
    mb.showinfo(title, message, parent=root)


def warn(root: tk.Tk, title: str, message: str) -> None:
    import tkinter.messagebox as mb
    mb.showwarning(title, message, parent=root)


def error(root: tk.Tk, title: str, message: str) -> None:
    import tkinter.messagebox as mb
    mb.showerror(title, message, parent=root)
