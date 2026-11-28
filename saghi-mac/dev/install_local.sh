#!/usr/bin/env bash
# Install the code on origin/main into the installed Saghi.app ("in-place fix").
#
# Why: an in-place install used to keep the old release number, so the app's
# own updater saw an older GitHub release as "newer" and overwrote the fixes.
# This script stamps the version as <latest release>.<n> (e.g. 0.3.3.1), which
# sorts above that release and below the next one. The next real release
# (built from main) replaces it as normal.
#
# Only origin/main is installed -- never a local branch or uncommitted files.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${SAGHI_APP:-$HOME/Applications/Saghi.app}"
PKG="$APP/Contents/Resources/app/saghi"
[ -d "$PKG" ] || { echo "Saghi.app not found at $APP" >&2; exit 1; }

cd "$REPO"
git fetch -q origin main --tags
BASE="$(git describe --tags --abbrev=0 --match 'v[0-9]*' origin/main | sed 's/^v//')"
N="$(git rev-list --count "v$BASE..origin/main")"
if [ "$N" = "0" ]; then VERSION="$BASE"; else VERSION="$BASE.$N"; fi
SHA="$(git rev-parse --short origin/main)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git archive origin/main saghi-mac/saghi | tar -x -C "$TMP"
REQ="$(sed -n 's/^REQUIREMENTS_SHA256 = //p' "$PKG/_version.py")"
cat > "$TMP/saghi-mac/saghi/_version.py" <<EOF
# Written by saghi-mac/dev/install_local.sh from origin/main $SHA -- do not edit.
__version__ = "$VERSION"
REQUIREMENTS_SHA256 = $REQ
EOF

# Keep one backup of what is being replaced (same name the updater uses).
rm -rf "$PKG.previous"
cp -R "$PKG" "$PKG.previous"
rsync -a --delete --exclude __pycache__ "$TMP/saghi-mac/saghi/" "$PKG/"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" \
  -c "Set :CFBundleVersion $VERSION" "$APP/Contents/Info.plist" || true

echo "Installed origin/main $SHA as version $VERSION"
if [ "${1:-}" = "--restart" ]; then
  pkill -f "$APP/Contents/MacOS/Saghi" || true
  sleep 2
  open "$APP"
fi
