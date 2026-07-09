"""QApplication + asyncio event loop bootstrap."""

import asyncio
import os
import sys

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from cqd.ui import settings_store
from cqd.ui.main_window import MainWindow


def run() -> int:
    load_dotenv()

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

    window = MainWindow()
    window.show()
    # Panel loads are pending tasks until the loop starts, so a first-run
    # choice made here still applies to the initial data load.
    window.maybe_show_first_run()

    with loop:
        loop.run_forever()
    return 0
