"""Build the static in-browser demo into web/dist/ (or the directory given).

    python scripts/build_web_demo.py [OUT_DIR]

The bundle is the pure engine only: `cqd/engine`, the two data modules it
needs (`normalize`, `crash_history` + its JSON) and `cqd/web`. Nothing from
`cqd/trading`, `cqd/ui`, credentials or the REST/WebSocket clients is shipped,
and the build fails if any of it, or any private-endpoint string, slips in.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
WEB = ROOT / "web"
STATIC = ["index.html", "style.css", "app.js", "worker.js", "_headers"]
ASSETS = {
    "favicon-32.png": ROOT / "site-kit/logo/favicon-32.png",
    "mark-256.png": ROOT / "site-kit/logo/mark-256.png",
    "card-1200x675.png": ROOT / "site-kit/card/card-1200x675.png",
}
ENGINE_FILES = [
    "cqd/__init__.py",
    "cqd/data/__init__.py",
    "cqd/data/normalize.py",
    "cqd/data/crash_history.py",
    "cqd/data/crash_history.json",
    "cqd/engine/__init__.py",
    *sorted(
        str(p.relative_to(SRC)).replace("\\", "/") for p in (SRC / "cqd/engine").glob("[!_]*.py")
    ),
    "cqd/web/__init__.py",
    "cqd/web/demo.py",
]
#: Strings that must never appear anywhere in the shipped demo.
FORBIDDEN = [
    "/0/private",
    "AddOrder",
    "CancelOrder",
    "API-Key",
    "API-Sign",
    "keyring",
    "cqd.trading",
    "cqd.ui",
]


def build(out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in STATIC:
        shutil.copy2(WEB / name, out / name)
    for name, src in ASSETS.items():
        shutil.copy2(src, out / name)

    zpath = out / "cqd-engine.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in ENGINE_FILES:
            # fixed timestamp: the same source gives a byte-identical bundle
            info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (SRC / rel).read_bytes())

    problems = check(out)
    if problems:
        raise SystemExit("web demo build refused:\n  " + "\n  ".join(problems))
    manifest = {
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            for p in sorted(out.iterdir())
            if p.is_file()
        },
        "engine_modules": ENGINE_FILES,
        "bytes": sum(p.stat().st_size for p in out.iterdir() if p.is_file()),
    }
    (out / "build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def check(out: Path) -> list[str]:
    problems = []
    with zipfile.ZipFile(out / "cqd-engine.zip") as z:
        names = z.namelist()
        for n in names:
            if n.startswith(("cqd/trading", "cqd/ui", "cqd/analyst", "cqd/alerts")):
                problems.append(f"bundle contains {n}")
            if n.endswith(("rest.py", "ws.py", "credentials.py", "client.py")):
                problems.append(f"bundle contains a network/credential module {n}")
        texts = {n: z.read(n).decode("utf-8", "replace") for n in names}
    for name in STATIC:
        texts[name] = (out / name).read_text(encoding="utf-8")
    for name, text in texts.items():
        for bad in FORBIDDEN:
            if bad in text:
                problems.append(f"{name} contains forbidden string {bad!r}")
    return problems


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else WEB / "dist"
    m = build(target)
    print(f"built {target} ({m['bytes'] / 1024:.0f} KB, {len(m['engine_modules'])} engine files)")
