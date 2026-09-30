import sys

from PySide6.QtWidgets import QApplication

from control_window import ControlWindow
from styles import STYLE_SHEET


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ARXIIV.STUDIO")
    app.setStyleSheet(STYLE_SHEET)

    window = ControlWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
