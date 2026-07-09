#!/usr/bin/env bash
# Phase 2 packaging: build the .app, then wrap it in a distributable .dmg.
#
# The .dmg (and the .app) are UNSIGNED - there is no Apple Developer account yet,
# so signing/notarization are deferred. End users bypass Gatekeeper once via
# right-click -> Open (see packaging/README.md).
#
# Usage (from anywhere):  bash packaging/build_dmg.sh
set -euo pipefail

VERSION="0.2.0"  # keep in sync with packaging/crypto-quant-desk.spec
VOLNAME="Crypto Quant Desk"
APP_NAME="Crypto Quant Desk.app"

# Resolve repo root from this script's location, and run from there.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
cd "$REPO"

APP_PATH="dist/$APP_NAME"
DMG_PATH="dist/Crypto-Quant-Desk-${VERSION}.dmg"

echo "==> Rebuilding the .app (clean)"
rm -rf build dist
pyinstaller packaging/crypto-quant-desk.spec

if [[ ! -d "$APP_PATH" ]]; then
  echo "ERROR: $APP_PATH was not produced by PyInstaller." >&2
  exit 1
fi

echo "==> Stripping extended attributes (resource-fork / Finder detritus)"
# Removes the 'resource fork ... not allowed' detritus the Phase 1 ad-hoc sign
# tripped on. This does NOT sign the bundle; it only cleans xattrs.
xattr -cr "$APP_PATH"

rm -f "$DMG_PATH"

if command -v create-dmg >/dev/null 2>&1; then
  echo "==> Building .dmg with create-dmg"
  create-dmg \
    --volname "$VOLNAME" \
    --app-drop-link 600 185 \
    --icon "$APP_NAME" 200 185 \
    --window-size 800 400 \
    "$DMG_PATH" \
    "$APP_PATH"
  DMG_TOOL="create-dmg"
else
  echo "==> create-dmg not found; building .dmg with hdiutil"
  # Stage the .app plus an /Applications symlink for drag-to-install.
  STAGE="$(mktemp -d)"
  cp -R "$APP_PATH" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  xattr -cr "$STAGE"  # clean detritus introduced by the copy
  hdiutil create \
    -volname "$VOLNAME" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG_PATH"
  rm -rf "$STAGE"
  DMG_TOOL="hdiutil"
fi

echo "==> Done. Built with: $DMG_TOOL"
ls -lh "$DMG_PATH"
echo "The .dmg is UNSIGNED. See packaging/README.md for the Gatekeeper bypass."
