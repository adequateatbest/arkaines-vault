#!/usr/bin/env python
"""Diablo 1 save editor -- GUI entry point (PySide6). Sits on top of the
same d1save/ core the CLI (cli.py) uses.

Usage:
    python gui_main.py [path/to/save.sv]
"""
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    initial_path = sys.argv[1] if len(sys.argv) > 1 else None
    window = MainWindow(initial_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
