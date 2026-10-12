#!/bin/bash
# Mac test session: the repo is the only home for work files; the Mac is a
# test machine. Everything this script creates lives in one temporary folder
# that `clean` deletes. It reuses the INSTALLED app's Python and model
# (~/Applications/Saghi.app), so nothing big is downloaded.
#
#   dev/mac_session.sh start    clone the branch into ~/.saghi-work and check the setup
#   dev/mac_session.sh mic      3-second microphone check (dev/mic_check.py)
#   dev/mac_session.sh model    transcribe an Arabic sample with the real model
#   dev/mac_session.sh app      run the app from the clone (Ctrl+C to quit) -- logs to the work folder
#   dev/mac_session.sh tests    run every dev/test_*.py
#   dev/mac_session.sh report   copy logs/results into docs/reports/<date>/, commit and push
#   dev/mac_session.sh clean    delete ~/.saghi-work (only if everything is pushed)
#
# Env: SAGHI_BRANCH (default claude/friendly-planck-ygkqn4), SAGHI_WORK (default ~/.saghi-work).

set -euo pipefail

REPO_URL="https://github.com/alpha2xyz/Saghi-mac.git"
BRANCH="${SAGHI_BRANCH:-claude/friendly-planck-ygkqn4}"
WORK="${SAGHI_WORK:-$HOME/.saghi-work}"
CLONE="$WORK/Saghi-mac"
OUT="$WORK/out"                       # logs and results waiting for `report`
APP_RES="$HOME/Applications/Saghi.app/Contents/Resources"
PY="$APP_RES/venv/bin/python3.12"
MODEL="$APP_RES/model"

say() { printf '%s\n' "$*"; }
die() { say "خطأ: $*" >&2; exit 1; }

need_clone() { [ -d "$CLONE/.git" ] || die "ما فيه نسخة عمل. شغّل: $0 start"; }

run_py() {
  # The clone's code, the installed app's Python and model, data/logs in the work folder.
  ( cd "$CLONE/saghi-mac" && PYTHONPATH=. SAGHI_MODEL_DIR="$MODEL" SAGHI_DATA_DIR="$OUT/data" "$PY" "$@" )
}

cmd="${1:-}"
case "$cmd" in
  start)
    [ "$(uname -s)" = "Darwin" ] || die "هذا السكربت للماك فقط"
    [ -x "$PY" ] || die "التطبيق مو مثبّت ($PY). ثبّته من صفحة الإصدارات أول."
    [ -f "$MODEL/config.json" ] || die "النموذج مو موجود في $MODEL"
    mkdir -p "$WORK" "$OUT/data"
    if [ -d "$CLONE/.git" ]; then
      git -C "$CLONE" fetch -q origin "$BRANCH"
      git -C "$CLONE" checkout -q "$BRANCH"
      git -C "$CLONE" pull -q --ff-only origin "$BRANCH"
    else
      git clone -q --branch "$BRANCH" "$REPO_URL" "$CLONE"
    fi
    say "جاهز: $CLONE  (الفرع $BRANCH)"
    say "بايثون: $PY"
    say "النموذج: $MODEL"
    ;;
  mic)
    need_clone; mkdir -p "$OUT"
    run_py dev/mic_check.py 2>&1 | tee "$OUT/mic_check.txt"
    ;;
  model)
    need_clone; mkdir -p "$OUT"
    WAV="${2:-$MODEL/examples/sample1.wav}"
    [ -f "$WAV" ] || die "ما لقيت ملف صوت: $WAV"
    run_py -m saghi.cli transcribe "$WAV" 2>&1 | tee "$OUT/model_transcribe.txt"
    ;;
  app)
    need_clone; mkdir -p "$OUT/data"
    say "التطبيق يشتغل من نسخة العمل. السجل: $OUT/data/saghi.log  (Ctrl+C للخروج)"
    say "اقفل نسخة صاغي المثبتة قبل، عشان ما يتعارض الاختصار."
    run_py -m saghi.ui.app || true
    ;;
  tests)
    need_clone; mkdir -p "$OUT"
    : > "$OUT/tests.txt"
    for t in "$CLONE"/saghi-mac/dev/test_*.py; do
      name="$(basename "$t")"
      if ( cd "$CLONE/saghi-mac" && PYTHONPATH=. SAGHI_MODEL_DIR="$MODEL" SAGHI_DATA_DIR="$(mktemp -d "$WORK/tmp.XXXX")" \
           SAGHI_TEST_WAV="$MODEL/examples/sample1.wav" "$PY" "dev/$name" ) > "$OUT/test_$name.log" 2>&1; then
        say "PASS $name" | tee -a "$OUT/tests.txt"
      else
        say "FAIL $name  (التفاصيل: $OUT/test_$name.log)" | tee -a "$OUT/tests.txt"
      fi
    done
    ;;
  report)
    need_clone
    DEST_REL="docs/reports/$(date +%Y-%m-%d_%H%M)"
    DEST="$CLONE/$DEST_REL"
    mkdir -p "$DEST"
    [ -f "$OUT/data/saghi.log" ] && cp "$OUT/data/saghi.log" "$DEST/"
    [ -f "$OUT/data/crash.log" ] && cp "$OUT/data/crash.log" "$DEST/"
    cp "$OUT"/*.txt "$OUT"/*.log "$DEST/" 2>/dev/null || true
    { say "# Mac test report"; say; say "- date: $(date)"; say "- macOS: $(sw_vers -productVersion)";
      say "- machine: $(sysctl -n machdep.cpu.brand_string 2>/dev/null || uname -m)"; say "- commit: $(git -C "$CLONE" rev-parse --short HEAD)"; } > "$DEST/README.md"
    git -C "$CLONE" add "$DEST_REL"
    git -C "$CLONE" add -u
    git -C "$CLONE" commit -q -m "Mac test report $DEST_REL" || say "ما فيه شي جديد للحفظ"
    git -C "$CLONE" push -q origin "$BRANCH"
    say "انرفع: $DEST_REL"
    ;;
  clean)
    [ -d "$WORK" ] || { say "ما فيه شي ينحذف."; exit 0; }
    if [ -d "$CLONE/.git" ]; then
      [ -z "$(git -C "$CLONE" status --porcelain)" ] || die "فيه تعديلات ما انحفظت. شغّل: $0 report"
      git -C "$CLONE" fetch -q origin "$BRANCH"
      [ -z "$(git -C "$CLONE" log --oneline "origin/$BRANCH..HEAD")" ] || die "فيه commits ما انرفعت. شغّل: git push"
    fi
    rm -rf "$WORK"
    say "انحذف $WORK. التطبيق المثبّت ما انلمس."
    ;;
  *)
    sed -n '2,15p' "$0"; exit 1
    ;;
esac
