"""Capture the README / site screenshots from the real app in demo mode.

Runs the full MainWindow against the demo book (no keys, no network calls that
need an account), visits each page, and grabs the window. On Linux run it under
a virtual display so fonts render:

    CQD_DATA_SOURCE=demo xvfb-run -a -s "-screen 0 1600x1000x24" \
        python scripts/capture_screenshots.py out/

Use a throwaway HOME so your own settings and layouts are not touched, and
enable the strategy (`[strategy] enabled=true`) there so the Signals page fills.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

# file stem, page, panel to focus, optional tab index inside that panel
SHOTS: list[tuple[str, str, str, int | None]] = [
    ("01-portfolio", "Portfolio", "overview", None),
    ("02-markets", "Markets", "chart", None),
    ("03-trading", "Trading", "ticket", None),
    ("04-signals", "Signals", "signals", None),
    ("05-risk-lab-var", "Risk", "lab", 0),
    ("06-risk-lab-crashes", "Risk", "lab", 1),
]

WARMUP_MS = int(os.environ.get("SHOT_WARMUP", 25000))  # demo stream + first loads
DELAY_MS = int(os.environ.get("SHOT_DELAY", 4500))  # per page, lets lazy panels load
SIZE = (1600, 1000)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
    out.mkdir(parents=True, exist_ok=True)

    from cqd.ui.assets import app_icon, load_fonts
    from cqd.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Crypto Quant Desk")
    app.setOrganizationName("cqd")
    load_fonts()
    app.setWindowIcon(app_icon())
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    window = MainWindow()
    window.resize(*SIZE)
    window.show()

    def take(i: int = 0) -> None:
        if i >= len(SHOTS):
            loop.stop()
            return
        stem, page, focus, tab = SHOTS[i]
        window._show_page(page, focus)
        if tab is not None:
            getattr(window, f"{focus}_panel").tabs.setCurrentIndex(tab)

        def grab() -> None:
            path = out / f"{stem}.png"
            window.grab().save(str(path))
            print("saved", path, flush=True)
            take(i + 1)

        QTimer.singleShot(DELAY_MS, grab)

    QTimer.singleShot(WARMUP_MS, take)
    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
