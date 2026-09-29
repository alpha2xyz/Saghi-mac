#!/bin/bash
# Saghi-mac: one-file installer for Apple-silicon Macs.
# Downloads the newest app release (tag v<number>) from GitHub, verifies it, and runs the bundled installer.
# تثبيت صاغي لنظام Mac: ينزّل أحدث إصدار، يتحقق منه، ثم يشغّل المثبّت المرفق.
#
# Optional environment variables:
#   SAGHI_RELEASE_TAG   install a specific release tag (for example v0.1.0) instead of the latest
#
# Uses only tools that ship with macOS (curl, shasum, ditto, grep, sed). No gh, no python.

set -euo pipefail

REPO="alpha2xyz/Saghi-mac"
API="https://api.github.com/repos/$REPO/releases"
MIN_MACOS_MAJOR=13
NEED_FREE_GB=12

say() { printf '%s\n' "$1"; }
fail() {
  say ""
  say "ERROR: $1"
  say "خطأ: $2"
  exit 1
}

WORK=""
cleanup() {
  if [ -n "$WORK" ] && [ -d "$WORK" ]; then
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

say ""
say "===================================================="
say "  Saghi-mac installer"
say "  تثبيت صاغي لنظام Mac"
say "===================================================="
say ""

# ---- checks -------------------------------------------------------------
if [ "$(uname -s)" != "Darwin" ]; then
  fail "This installer is for macOS only." "هذا المثبّت لنظام macOS فقط."
fi
if [ "$(uname -m)" != "arm64" ]; then
  fail "Saghi-mac needs a Mac with Apple silicon (M1 or newer). Intel Macs are not supported." \
       "يحتاج صاغي إلى جهاز Mac بمعالج Apple silicon (M1 أو أحدث). أجهزة Intel غير مدعومة."
fi
MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$MACOS_MAJOR" -lt "$MIN_MACOS_MAJOR" ]; then
  fail "Saghi-mac needs macOS $MIN_MACOS_MAJOR or newer (you have $(sw_vers -productVersion))." \
       "يحتاج صاغي إلى macOS $MIN_MACOS_MAJOR أو أحدث (لديك $(sw_vers -productVersion))."
fi

TMP_BASE="${TMPDIR:-/tmp}"
free_gb() { df -Pk "$1" | awk 'NR==2 {printf "%d", $4/1048576}'; }
for d in "$TMP_BASE" "$HOME"; do
  FREE="$(free_gb "$d")"
  if [ "$FREE" -lt "$NEED_FREE_GB" ]; then
    fail "Not enough free disk space on the drive holding $d: ${FREE} GB free, about ${NEED_FREE_GB} GB needed." \
         "المساحة الفارغة غير كافية على القرص الذي يحتوي $d: المتاح ${FREE} GB والمطلوب حوالي ${NEED_FREE_GB} GB."
  fi
done

# ---- find the release ------------------------------------------------------
say "Looking up the release... / جارٍ البحث عن الإصدار..."
WORK="$(mktemp -d "$TMP_BASE/saghi-mac-install.XXXXXX")"
if [ -n "${SAGHI_RELEASE_TAG:-}" ]; then
  RELEASE_URL="$API/tags/$SAGHI_RELEASE_TAG"
else
  # Newest release whose tag looks like v<number> (skips the model-v1 release).
  if ! curl -fsSL --retry 3 -H "Accept: application/vnd.github+json" -o "$WORK/list.json" "$API?per_page=20"; then
    fail "Could not read the release list from GitHub. Check your internet connection." \
         "تعذّر قراءة قائمة الإصدارات من GitHub. تحقق من اتصال الإنترنت."
  fi
  # The API lists releases newest first; take the first tag that matches ^v[0-9].
  LATEST_TAG="$({ grep -o '"tag_name": *"[^"]*"' "$WORK/list.json" || true; } | sed 's/.*: *"\(.*\)"/\1/' | grep -E '^v[0-9]' | head -1 || true)"
  if [ -z "$LATEST_TAG" ]; then
    fail "No app release found (a tag like v0.1.0)." "لم يُعثر على إصدار للتطبيق (وسم مثل v0.1.0)."
  fi
  RELEASE_URL="$API/tags/$LATEST_TAG"
fi
if ! curl -fsSL --retry 3 -H "Accept: application/vnd.github+json" -o "$WORK/release.json" "$RELEASE_URL"; then
  fail "Could not read the release info from GitHub. Check your internet connection." \
       "تعذّر قراءة معلومات الإصدار من GitHub. تحقق من اتصال الإنترنت."
fi

TAG="$(grep -o '"tag_name": *"[^"]*"' "$WORK/release.json" | head -1 | sed 's/.*: *"\(.*\)"/\1/' || true)"
{ grep -o '"browser_download_url": *"[^"]*"' "$WORK/release.json" || true; } | sed 's/.*: *"\(.*\)"/\1/' > "$WORK/urls.txt"
SUMS_URL="$(grep '/SHA256SUMS$' "$WORK/urls.txt" | head -1 || true)"
grep -E '/Saghi-mac-[^/]*\.zip\.part-[a-z]+$' "$WORK/urls.txt" | sort > "$WORK/parts.txt" || true
if [ -z "$SUMS_URL" ] || [ ! -s "$WORK/parts.txt" ]; then
  fail "Release ${TAG:-?} has no app files. Set SAGHI_RELEASE_TAG to an app release, for example: SAGHI_RELEASE_TAG=v0.1.0" \
       "الإصدار ${TAG:-?} لا يحتوي ملفات التطبيق. حدّد إصدارًا بهذا الشكل: SAGHI_RELEASE_TAG=v0.1.0"
fi
say "Release: $TAG / الإصدار: $TAG"
say "Files: $(wc -l < "$WORK/parts.txt" | tr -d ' ') part(s) / عدد الأجزاء: $(wc -l < "$WORK/parts.txt" | tr -d ' ')"
say ""

# ---- download ------------------------------------------------------------
say "Downloading... this can take a while. / جارٍ التنزيل... قد يستغرق وقتًا."
curl -fsSL --retry 3 -o "$WORK/SHA256SUMS" "$SUMS_URL" \
  || fail "Could not download SHA256SUMS." "تعذّر تنزيل ملف SHA256SUMS."

verify_file() {
  # $1 = file name inside $WORK; checks it against the matching line in SHA256SUMS
  local name="$1" expected actual
  expected="$(grep -E "[ *]${name}\$" "$WORK/SHA256SUMS" | head -1 | awk '{print $1}')"
  [ -n "$expected" ] || fail "No checksum found for $name." "لا يوجد مجموع تحقق للملف $name."
  actual="$(shasum -a 256 "$WORK/$name" | awk '{print $1}')"
  if [ "$expected" != "$actual" ]; then
    fail "Checksum mismatch for $name. The download is damaged. Please run the installer again." \
         "مجموع التحقق للملف $name غير مطابق. التنزيل تالف. أعد تشغيل المثبّت."
  fi
}

PART_NAMES=()
while IFS= read -r url; do
  name="${url##*/}"
  say "  - $name"
  curl -fL --retry 3 --progress-bar -o "$WORK/$name" "$url" \
    || fail "Download failed: $name" "فشل تنزيل: $name"
  verify_file "$name"
  PART_NAMES+=("$name")
done < "$WORK/parts.txt"
say "Checksums OK. / مجاميع التحقق سليمة."

# ---- join (delete each part right after it is appended, to save disk) ----------
ZIP_NAME="$(printf '%s\n' "${PART_NAMES[0]}" | sed 's/\.part-[a-z]*$//')"
say "Joining parts... / جارٍ دمج الأجزاء..."
: > "$WORK/$ZIP_NAME"
for name in "${PART_NAMES[@]}"; do
  cat "$WORK/$name" >> "$WORK/$ZIP_NAME"
  rm -f "$WORK/$name"
done
verify_file "$ZIP_NAME"

# ---- extract ---------------------------------------------------------------
say "Unpacking... / جارٍ فك الضغط..."
mkdir -p "$WORK/extract"
ditto -x -k "$WORK/$ZIP_NAME" "$WORK/extract" \
  || fail "Could not unpack the download." "تعذّر فك ضغط الملف."
rm -f "$WORK/$ZIP_NAME"

BUNDLE="$WORK/extract/Saghi-Mac-Portable"
if [ ! -f "$BUNDLE/install-saghi.command" ]; then
  fail "The download does not contain the installer." "الملف المنزَّل لا يحتوي المثبّت."
fi

# ---- run the bundled installer ------------------------------------------------
say ""
say "Installing Saghi... / جارٍ تثبيت صاغي..."
# Not 'exec': the temp folder must be removed by the trap after this finishes.
bash "$BUNDLE/install-saghi.command"
