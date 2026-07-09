"""QApplication + asyncio event loop bootstrap."""

import asyncio
import sys

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from cqd.ui.main_window import MainWindow


def run() -> int:
    load_dotenv()

    app = QApplication(sys.argv)
    app.setApplicationName("Crypto Quant Desk")
    app.setOrganizationName("cqd")
    # The theme stylesheet is applied by MainWindow from the theme registry.

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    with loop:
        loop.run_forever()
    return 0
