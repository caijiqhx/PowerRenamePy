# -*- coding: utf-8 -*-
"""
PowerRenamePy 入口。

运行（在项目根目录）：python src/main.py
要求：Python 3.10+，需自带 tkinter（Windows 官方安装包默认包含）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ui import PowerRenameApp


def main() -> None:
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    PowerRenameApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
