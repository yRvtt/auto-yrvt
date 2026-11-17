"""Entrada do Auto yRvt.

`python -m auto_yrvt` ou `python run.py` abrem a interface.
"""

import ctypes
import sys


def _dpi_aware():
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    _dpi_aware()
    try:
        import win32api  # noqa: F401
        import win32con  # noqa: F401
        import win32gui  # noqa: F401
    except ImportError:
        print("Falta o pywin32. Rode: pip install pywin32")
        return 1

    try:
        from .ui import App
    except Exception as exc:
        print(f"Erro ao carregar a interface: {exc}")
        return 1

    try:
        app = App()
    except Exception as exc:
        try:
            from tkinter import messagebox
            messagebox.showerror("Auto yRvt", f"Erro ao iniciar:\n{exc}")
        except Exception:
            print(f"Erro ao iniciar: {exc}")
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
