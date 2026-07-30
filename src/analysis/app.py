from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


STYLE = """
QMainWindow, QWidget {
    background: #232a34;
    color: #e7ebef;
}
QMenuBar, QMenu, QToolBar, QStatusBar {
    background: #1b2129;
    color: #e7ebef;
}
QTreeWidget, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #171c23;
    color: #e7ebef;
    border: 1px solid #3b4654;
    selection-background-color: #426c8a;
}
QTreeWidget::item { padding: 4px; }
QTabWidget::pane { border: 1px solid #3b4654; }
QTabBar::tab {
    background: #1b2129;
    padding: 8px 14px;
    border: 1px solid #3b4654;
}
QTabBar::tab:selected { background: #354253; }
QPushButton {
    background: #354253;
    border: 1px solid #506075;
    padding: 6px 12px;
}
QToolTip {
    color: #111;
    background: #f2f2f2;
}
"""


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("BelleFlow Studio")
    application.setOrganizationName("BelleFlow")
    application.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
