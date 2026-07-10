"""Session-wide test config.

Force Qt onto the offscreen platform before any QApplication is created, so the
GUI/QtAds tests run headless and deterministically. Without this, the native
Windows Qt platform segfaults during QtAds multi-manager teardown in the full
suite (the pre-commit hook runs pytest with no QT_QPA_PLATFORM set, unlike CI,
which sets offscreen explicitly). `setdefault` leaves any explicit override
(CI, or a dev deliberately choosing a platform) untouched.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
