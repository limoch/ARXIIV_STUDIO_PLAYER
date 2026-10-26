"""Application entry point with persistent crash diagnostics."""
import logging
from logging.handlers import RotatingFileHandler
import os
import sys

from PySide6.QtCore import QStandardPaths, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QMessageBox

from control_window import ControlWindow
from styles import STYLE_SHEET

_LOGGER = logging.getLogger("arxiiv_studio")


def _configure_logging():
    try:
        root = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
        if not root:
            root = os.path.join(os.path.expanduser("~"), ".arxiiv_studio")
        os.makedirs(root, exist_ok=True)
        log_path = os.path.join(root, "arxiiv_studio.log")
        handler = RotatingFileHandler(
            log_path, maxBytes=1_500_000, backupCount=3, encoding="utf-8"
        )
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[handler],
            force=True,
        )
        _LOGGER.info("Application starting; Python %s", sys.version.replace("\n", " "))
        return log_path
    except Exception:
        logging.basicConfig(level=logging.ERROR)
        _LOGGER.exception("Could not initialize file logging")
        return None


def _exception_hook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    _LOGGER.critical(
        "Uncaught Python exception",
        exc_info=(exc_type, exc_value, exc_tb),
    )
    try:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass


def _qt_message_handler(mode, context, message):
    # Qt multimedia/backend warnings are often the only clue when a codec or
    # graphics backend fails. Keep these in the same rotating diagnostic log.
    try:
        level = int(mode)
        if level >= 3:
            _LOGGER.error("Qt: %s (file=%s line=%s)", message, context.file, context.line)
        elif level == 2:
            _LOGGER.warning("Qt: %s", message)
        else:
            _LOGGER.info("Qt: %s", message)
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ARXIIV.STUDIO")
    app.setOrganizationName("ARXIIV.STUDIO")
    log_path = _configure_logging()
    sys.excepthook = _exception_hook
    qInstallMessageHandler(_qt_message_handler)
    app.setStyleSheet(STYLE_SHEET)

    try:
        window = ControlWindow()
        window.show()
    except Exception:
        _LOGGER.exception("Fatal error while creating the main window")
        if log_path:
            QMessageBox.critical(
                None,
                "ARXIIV.STUDIO could not start",
                "An error occurred while starting the program.\n\n"
                f"Diagnostic log: {log_path}",
            )
        raise

    app.aboutToQuit.connect(lambda: _LOGGER.info("Application shutdown"))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
