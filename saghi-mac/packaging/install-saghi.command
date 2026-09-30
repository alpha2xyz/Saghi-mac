#!/bin/bash
# Saghi for Mac -- installer.
#
# Double-click this file in Finder (from inside the Saghi-Mac-Portable
# folder) to install Saghi. First run: Gatekeeper will block it since it's
# unsigned -- see INSTALL_AR.txt / README_AR.md for the exact steps
# (Control-click -> Open on macOS 13/14, or System Settings -> Privacy &
# Security -> "Open Anyway" on macOS 15+).
#
# What this does (copy everything into a stable per-user location,
# independent of where this portable folder happens to sit):
#   1. Copies python-runtime/, the saghi/ source, and model/ into
#      ~/Applications/Saghi.app/Contents/Resources/ -- a real, mac-native
#      .app bundle so Saghi shows up as "Saghi" (not "python3.12") in the
#      Dock, Force Quit, and -- best effort, UNVERIFIED until real
#      Apple-Silicon testing -- in Privacy & Security permission prompts.
#   2. Creates a venv from the copied python-runtime interpreter.
#   3. Installs the pre-downloaded arm64 wheels OFFLINE (--no-index
#      --find-links), no network access needed.
#   4. Asks (y/n, default: no) whether to enable launch-at-login via a
#      LaunchAgent.
#   5. Launches Saghi (skip with SAGHI_NO_LAUNCH=1, used by CI).
#
# Requires nothing from the internet. Safe to re-run.

set -euo pipefail

# ---- locate ourselves -------------------------------------------------
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications/Saghi.app"
RES_DIR="$APP_DIR/Contents/Resources"
MACOS_DIR="$APP_DIR/Contents/MacOS"
VENV_DIR="$RES_DIR/venv"
PY_RUNTIME_SRC="$BUNDLE_DIR/python-runtime"
PY_BIN_NAME="python3.12"

say() { printf '%s\n' "$1"; }

say ""
say "===================================================="
say "  تثبيت صاغي لنظام Mac"
say "===================================================="
say ""

# ---- sanity checks ------------------------------------------------------
if [ ! -x "$PY_RUNTIME_SRC/bin/$PY_BIN_NAME" ]; then
  say "خطأ: مجلد python-runtime غير مكتمل بجانب هذا الملف."
  say "تأكد من نسخ مجلد Saghi-Mac-Portable كاملًا، وليس هذا الملف وحده."
  exit 1
fi
if [ ! -f "$BUNDLE_DIR/model/model.safetensors" ]; then
  say "خطأ: مجلد model غير مكتمل بجانب هذا الملف."
  say "تأكد من نسخ مجلد Saghi-Mac-Portable كاملًا (النموذج ~4.1GB)."
  exit 1
fi
if [ ! -d "$BUNDLE_DIR/saghi" ] || [ ! -f "$BUNDLE_DIR/requirements-arm64.txt" ] || [ ! -d "$BUNDLE_DIR/wheels" ]; then
  say "خطأ: الحزمة غير مكتملة (saghi/ أو requirements-arm64.txt أو wheels/ مفقود)."
  exit 1
fi

# arm64-only check -- this bundle cannot run on Intel Macs
# (Apple Silicon only).
ARCH="$(uname -m)"
if [ "$ARCH" != "arm64" ]; then
  say "تنبيه: هذا الجهاز ليس Apple Silicon (المعالج: $ARCH)."
  say "صاغي لنظام Mac مبني خصيصًا لمعالجات Apple Silicon (M1 فأحدث) فقط."
  say "لن يعمل بايثون المرفق مع هذه الحزمة على معالج Intel."
  exit 1
fi

if pgrep -f "saghi\.ui\.app" >/dev/null 2>&1; then
  say "أغلق صاغي أولًا من قائمة شريط القوائم، ثم أعد تشغيل هذا الملف."
  exit 1
fi

# ---- 1. copy runtime + app + model into the stable location -----------
say "1) نسخ الملفات إلى $APP_DIR ..."
mkdir -p "$MACOS_DIR" "$RES_DIR/app"

if [ ! -x "$RES_DIR/python-runtime/bin/$PY_BIN_NAME" ]; then
  say "   - نسخ بايثون المرفق (مرة واحدة فقط)..."
  ditto "$PY_RUNTIME_SRC" "$RES_DIR/python-runtime"
else
  say "   - بايثون المرفق موجود مسبقًا، تخطي."
fi

say "   - نسخ كود صاغي..."
ditto "$BUNDLE_DIR/saghi" "$RES_DIR/app/saghi"
if [ -f "$BUNDLE_DIR/AppIcon.icns" ]; then
  cp "$BUNDLE_DIR/AppIcon.icns" "$RES_DIR/AppIcon.icns"
fi

say "   - نسخ النموذج المحلي (~4.1GB، قد يستغرق دقائق)..."
rm -rf "$RES_DIR/model"
# Try an instant copy-on-write clone first (APFS, same volume: uses no extra
# disk space); fall back to a normal copy.
if ! cp -Rc "$BUNDLE_DIR/model" "$RES_DIR/model" 2>/dev/null; then
  rm -rf "$RES_DIR/model"
  ditto "$BUNDLE_DIR/model" "$RES_DIR/model"
fi

# ---- Info.plist (identifies the process as "Saghi", not "python3.12") -
# Version comes from the stamp build_bundle.py writes into saghi/_version.py.
APP_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$BUNDLE_DIR/saghi/_version.py" 2>/dev/null || true)"
APP_VERSION="${APP_VERSION:-0.1.0}"
cat > "$APP_DIR/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Saghi</string>
    <key>CFBundleDisplayName</key>
    <string>صاغي</string>
    <key>CFBundleIdentifier</key>
    <string>io.github.alpha2xyz.saghi-mac</string>
    <key>CFBundleVersion</key>
    <string>$APP_VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$APP_VERSION</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleExecutable</key>
    <string>Saghi</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <false/>
    <key>NSMicrophoneUsageDescription</key>
    <string>يحتاج صاغي إلى الميكروفون لتحويل صوتك إلى نص.</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# ---- launcher script ----------------------------------------------------
cat > "$MACOS_DIR/Saghi" << LAUNCHER
#!/bin/bash
# Saghi launcher -- resolves paths relative to this .app bundle so it
# works regardless of where ~/Applications/Saghi.app physically sits.
DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")/../Resources" && pwd)"
export PYTHONPATH="\$DIR/app"
export SAGHI_MODEL_DIR="\$DIR/model"
exec "\$DIR/venv/bin/$PY_BIN_NAME" -m saghi.ui.app
LAUNCHER
chmod +x "$MACOS_DIR/Saghi"

# ---- 2. venv from the copied (now-stable-path) interpreter -------------
if [ ! -x "$VENV_DIR/bin/$PY_BIN_NAME" ]; then
  say "2) إنشاء بيئة بايثون..."
  "$RES_DIR/python-runtime/bin/$PY_BIN_NAME" -m venv "$VENV_DIR"
else
  say "2) بيئة بايثون موجودة مسبقًا، تخطي الإنشاء."
fi

# ---- 3. offline wheel install -------------------------------------------
say "3) تثبيت المكتبات (بدون إنترنت، من الحزمة المرفقة)..."
"$VENV_DIR/bin/pip" install --no-index --find-links "$BUNDLE_DIR/wheels" \
  -r "$BUNDLE_DIR/requirements-arm64.txt" --quiet
say "   تم."

# ---- 4. optional launch-at-login ----------------------------------------
say ""
printf '%s' "4) هل تريد تشغيل صاغي تلقائيًا عند الدخول إلى الجهاز؟ (y/N): "
read -r ENABLE_LOGIN_ITEM || ENABLE_LOGIN_ITEM=""
if [[ "$ENABLE_LOGIN_ITEM" =~ ^[Yy]$ ]]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  PLIST_DEST="$HOME/Library/LaunchAgents/io.github.alpha2xyz.saghi-mac.plist"
  sed "s#__SAGHI_APP_DIR__#$APP_DIR#g; s#__PY_BIN__#$PY_BIN_NAME#g" \
    "$BUNDLE_DIR/io.github.alpha2xyz.saghi-mac.plist" > "$PLIST_DEST"
  launchctl unload "$PLIST_DEST" >/dev/null 2>&1 || true
  if launchctl load "$PLIST_DEST" 2>/dev/null; then
    say "   تم تفعيل التشغيل التلقائي."
  else
    say "   تعذّر تفعيله الآن تلقائيًا؛ سيعمل من الدخول التالي للجهاز."
  fi
else
  say "   تم التخطي (يمكن تفعيله لاحقًا من ملف io.github.alpha2xyz.saghi-mac.plist)."
fi

# ---- done ----------------------------------------------------------------
say ""
say "===================================================="
say "  تم تثبيت صاغي بنجاح في:"
say "  $APP_DIR"
say ""
say "  التذكيرات والصلاحيات المطلوبة (ميكروفون، Accessibility،"
say "  Input Monitoring): راجع README_AR.md."
say ""
say "  يمكنك الآن حذف مجلد Saghi-Mac-Portable هذا إذا رغبت --"
say "  التطبيق يعمل من نسخته الخاصة في $APP_DIR"
say "===================================================="
if [ "${SAGHI_NO_LAUNCH:-}" = "1" ]; then
  say ""
  say "SAGHI_NO_LAUNCH=1: skipping launch. / تم تخطي التشغيل."
else
  say ""
  say "تشغيل صاغي الآن..."
  open "$APP_DIR"
fi
