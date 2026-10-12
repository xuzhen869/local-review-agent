"""验证 AddFontResourceEx 私有注册字体后 tkinter 是否可用 Anton/Condiment。"""
import ctypes
import os
import sys
import tkinter as tk
import tkinter.font as tkfont

FR_PRIVATE = 0x10
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

r = tk.Tk()
r.withdraw()

for name in ("Anton-Regular.ttf", "Condiment-Regular.ttf"):
    p = os.path.join(root, "assets", "fonts", name)
    rc = ctypes.windll.gdi32.AddFontResourceExW(p, FR_PRIVATE, 0)
    print(f"AddFontResourceEx({name}) rc={rc}")

fams = set(tkfont.families())
print("Anton in families:", "Anton" in fams)
print("Condiment in families:", "Condiment" in fams)

# 用直接 family 名渲染测量：若真实使用该字体，measure 应明显不同于 Segoe UI 默认
f_anton = tkfont.Font(family="Anton", size=40)
f_segoe = tkfont.Font(family="Segoe UI", size=40, weight="bold")
f_cond = tkfont.Font(family="Condiment", size=30)
m_anton = f_anton.measure("AGENT")
m_segoe = f_segoe.measure("AGENT")
m_cond = f_cond.measure("Orbis")
print(f"Anton 'AGENT' width={m_anton}  SegoeBold={m_segoe}  相差={(m_anton - m_segoe) / m_segoe:+.0%}")
print(f"Condiment 'Orbis' width={m_cond}")
print("Anton actual name:", f_anton.actual("family"))
print("Condiment actual name:", f_cond.actual("family"))
r.destroy()
