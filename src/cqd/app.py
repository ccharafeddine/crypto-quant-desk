"""QApplication + asyncio event loop bootstrap."""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from cqd.data.paths import app_data_dir
from cqd.ui import settings_store
from cqd.ui.main_window import MainWindow

log = logging.getLogger("cqd")


def _setup_logging() -> None:
    """File log + crash hooks: a GUI app (esp. pythonw) must never die silently."""
    handler = logging.FileHandler(app_data_dir() / "app.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def _excepthook(exc_type, exc, tb):
        log.critical("UNHANDLED EXCEPTION", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook


def _loop_exception_handler(loop, context) -> None:
    """Log task exceptions instead of letting them vanish (or kill the app)."""
    exc = context.get("exception")
    if exc is not None:
        log.error("Async task failed: %s", context.get("message", ""), exc_info=exc)
    else:
        log.error("Async loop error: %s", context)


def run() -> int:
    load_dotenv()
    _setup_logging()
    log.info("starting Crypto Quant Desk")

    app = QApplication(sys.argv)
    app.setApplicationName("Crypto Quant Desk")
    app.setOrganizationName("cqd")
    # The theme stylesheet is applied by MainWindow from the theme registry.

    # Persisted data-source choice -> CQD_DATA_SOURCE, BEFORE any panel builds
    # a client. An explicitly set env var (dev override) is respected because
    # "auto" only clears the variable when the user never chose a source.
    if "CQD_DATA_SOURCE" not in os.environ:
        settings_store.apply_data_source_env()

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(_loop_exception_handler)

    window = MainWindow()
    window.show()
    # First-run (and the Settings dialog it can open) must run INSIDE the
    # started loop: its async verify uses ensure_future, and stepping tasks
    # while the loop is not yet running raises inside a Qt event handler,
    # which recent PySide6 treats as fatal (the silent-crash bug).
    QTimer.singleShot(0, window.maybe_show_first_run)

    with loop:
        loop.run_forever()
    log.info("clean shutdown")
    return 0
