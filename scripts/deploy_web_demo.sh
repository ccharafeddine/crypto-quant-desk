#!/usr/bin/env bash
# Deploy the static demo (web/dist) to Cloudflare Pages, served at cqd.chafic.me.
#
# This publishes a public website, so it only runs when asked to explicitly:
#
#   CQD_DEPLOY_APPROVED=yes scripts/deploy_web_demo.sh --confirm cqd.chafic.me
#
# Needs: Node (for npx wrangler) and either `wrangler login` or the
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID environment variables (a token
# with "Cloudflare Pages: Edit"). Nothing secret lives in this repo.
#
# One-time setup, done in the Cloudflare dashboard (wrangler cannot do it):
#   1. The first run creates the Pages project "$PROJECT".
#   2. Pages > $PROJECT > Custom domains > add cqd.chafic.me. If chafic.me's DNS
#      is on Cloudflare the CNAME is added for you; otherwise add
#      CNAME cqd -> $PROJECT.pages.dev at your DNS host.
set -euo pipefail

PROJECT="${CQD_PAGES_PROJECT:-cqd-demo}"
DOMAIN="cqd.chafic.me"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python}"

if [[ "${CQD_DEPLOY_APPROVED:-}" != "yes" || "${1:-}" != "--confirm" || "${2:-}" != "$DOMAIN" ]]; then
  echo "Refusing to deploy: this publishes $DOMAIN." >&2
  echo "Run: CQD_DEPLOY_APPROVED=yes $0 --confirm $DOMAIN" >&2
  exit 2
fi

cd "$ROOT"
if [[ -n "$(git status --porcelain -- src web scripts)" ]]; then
  echo "Refusing to deploy: uncommitted changes under src/, web/ or scripts/." >&2
  exit 3
fi

echo "==> tests"
QT_QPA_PLATFORM=offscreen "$PY" -m pytest -q tests/test_web_demo.py
echo "==> build"
"$PY" scripts/build_web_demo.py web/dist
echo "==> deploy $(git rev-parse --short HEAD) to Pages project $PROJECT"
npx --yes wrangler@4 pages deploy web/dist \
  --project-name "$PROJECT" \
  --branch main \
  --commit-hash "$(git rev-parse HEAD)" \
  --commit-message "$(git log -1 --format=%s)"
echo "==> done. Live at https://$DOMAIN once the custom domain is attached (see header)."
