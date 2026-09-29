"""
All user-visible Arabic strings for the GUI, as constants in one module
(so a future localization pass has a single place to work from -- per the
Phase 4 task's explicit "future localization" note).

MIXED-TEXT RULE: whenever an Arabic string embeds a Latin-script token
(a device name, "CPU"/"MPS", a filename) mid-sentence, the Latin run must
be isolated with Unicode bidi-isolate characters (LRI/PDI) so it renders
correctly in an RTL layout -- otherwise punctuation/spacing around the
Latin run can visually flip. This bug typically only shows up at narrow
widths, so it's handled here up front rather than discovered later. See
`isolate_ltr()`.
"""

from __future__ import annotations

# U+2066 LEFT-TO-RIGHT ISOLATE / U+2069 POP DIRECTIONAL ISOLATE -- wraps an
# embedded LTR run (device names, filenames, "CPU"/"MPS") inside RTL text
# so the bidi algorithm treats it as one opaque LTR unit and never lets
# neighboring Arabic punctuation/spacing get reordered around it. Preferred
# over a bare LRM mark for multi-character/multi-word runs.
_LRI = "⁦"
_PDI = "⁩"


def isolate_ltr(text: str) -> str:
    """Wrap an embedded Latin-script run so it renders correctly inside RTL text."""
    return f"{_LRI}{text}{_PDI}"


# ---- app-wide ---------------------------------------------------------

APP_TITLE = "صاغي"

# ---- tray ---------------------------------------------------------------

TRAY_OPEN = "فتح صاغي"
TRAY_QUIT = "إنهاء"
TRAY_ENGINE_PREFIX = "المحرك: "

# ---- engine status (shared by the tray menu line and the header chip) ---

ENGINE_COLD = "بارد"
ENGINE_LOADING = "جارٍ التحميل"
ENGINE_READY_SUFFIX = "جاهز"


def engine_ready_label(device: str) -> str:
    """e.g. 'CPU جاهز' or 'MPS جاهز' -- device name isolated per the mixed-text rule."""
    return f"{isolate_ltr((device or 'CPU').upper())} {ENGINE_READY_SUFFIX}"


def format_engine_status(state: str, device: str) -> str:
    """state: 'cold' | 'loading' | 'ready' (see ui/engine_status.py's STATE_* constants)."""
    if state == "loading":
        return ENGINE_LOADING
    if state == "ready":
        return engine_ready_label(device)
    return ENGINE_COLD


def format_tray_engine_line(state: str, device: str) -> str:
    return TRAY_ENGINE_PREFIX + format_engine_status(state, device)


# ---- sidebar navigation ---------------------------------------------------

NAV_HISTORY = "السجل"
NAV_FILEJOB = "تفريغ ملف"
NAV_SETTINGS = "الإعدادات"

# ---- history page -------------------------------------------------------

HISTORY_SEARCH_PLACEHOLDER = "ابحث في السجل…"
HISTORY_REFRESH = "تحديث"
HISTORY_COPY_BUTTON = "نسخ النص"
HISTORY_RAW_TOGGLE = "النص الخام"
HISTORY_EMPTY = "لا يوجد سجل بعد"

# ---- file transcription page ---------------------------------------------

FILEJOB_PICK_BUTTON = "اختيار ملف"
FILEJOB_DROP_HINT = "اسحب الملف هنا، أو اختره من الزر أعلاه"
FILEJOB_NO_FILE_PICKED = "لم يتم اختيار ملف بعد"
FILEJOB_LANGUAGE_LABEL = "اللغة"
FILEJOB_CLEANUP_LABEL = "مستوى التنظيف"
FILEJOB_TIMESTAMPS_LABEL = "إضافة التوقيت"
FILEJOB_START_BUTTON = "ابدأ التفريغ"
FILEJOB_CANCEL_BUTTON = "إلغاء"
FILEJOB_RESUME_NOTICE = "سيتم الاستكمال من آخر جزء محفوظ"
FILEJOB_OPEN_FOLDER = "فتح مجلد المخرجات"
FILEJOB_COPY_TEXT = "نسخ النص"
FILEJOB_ELAPSED_LABEL = "الوقت المنقضي"
FILEJOB_ETA_LABEL = "الوقت المتبقي التقريبي"
FILEJOB_ERROR_PREFIX = "تعذر التفريغ: "
FILEJOB_CANCELLED_NOTICE = "تم إلغاء العملية بعد آخر جزء مكتمل. يمكن إكمالها لاحقًا باختيار الملف نفسه."


def filejob_chunk_progress(done: int, total: int) -> str:
    return f"الجزء {done} من {total}"


def filejob_elapsed_text(elapsed_s: float) -> str:
    return f"{FILEJOB_ELAPSED_LABEL}: {_format_duration(elapsed_s)}"


def filejob_eta_text(eta_s) -> str:
    if eta_s is None:
        return f"{FILEJOB_ETA_LABEL}: —"
    return f"{FILEJOB_ETA_LABEL}: {_format_duration(eta_s)}"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return isolate_ltr(f"{hours}:{minutes:02d}:{secs:02d}")
    return isolate_ltr(f"{minutes}:{secs:02d}")


# ---- settings page --------------------------------------------------------

SETTINGS_HOTKEY_LABEL = "الاختصار"
SETTINGS_MIC_LABEL = "الميكروفون"
SETTINGS_CLEANUP_LABEL = "مستوى التنظيف"
SETTINGS_LANGUAGE_LABEL = "لغة التفريغ"
SETTINGS_AUTOPASTE_LABEL = "اللصق التلقائي"
SETTINGS_SAVE_RECORDINGS_LABEL = "حفظ التسجيلات"
SETTINGS_LAUNCH_AT_LOGIN_LABEL = "تشغيل عند بدء الجهاز"
SETTINGS_WAVEFORM_STYLE_LABEL = "شكل الموجة"
SETTINGS_WAVEFORM_COLOR_LABEL = "لون الموجة"

MIC_DEFAULT = "الافتراضي"

WAVEFORM_STYLE_LABELS = {
    "bars": "أعمدة",
    "line": "موجة خطية",
    "dots": "نقاط",
    "pulse": "نبض",
}

CLEANUP_LEVEL_LABELS = {
    "none": "بدون",
    "light": "خفيف",
    "medium": "متوسط",
}

LANGUAGE_LABELS = {
    "ar": "العربية",
    "en": "الإنجليزية",
}

HOTKEY_LABELS = {
    "ctrl+cmd": isolate_ltr("Ctrl + Cmd"),
    "alt+cmd": isolate_ltr("Alt + Cmd"),
}

# ---- OpenRouter rephrase (Phase 6) -----------------------------------------

SETTINGS_OPENROUTER_ENABLED_LABEL = "إعادة الصياغة بعد التفريغ"
SETTINGS_OPENROUTER_MODEL_LABEL = "معرّف النموذج"
SETTINGS_OPENROUTER_MODEL_PLACEHOLDER = isolate_ltr("google/gemini-3.1-flash-lite")
SETTINGS_OPENROUTER_KEY_LABEL = "مفتاح OpenRouter"
SETTINGS_OPENROUTER_KEY_SAVED_PLACEHOLDER = "●●●● (محفوظ)"
SETTINGS_OPENROUTER_KEY_EMPTY_PLACEHOLDER = "الصق المفتاح هنا"
SETTINGS_OPENROUTER_SAVE_BUTTON = "حفظ"
SETTINGS_OPENROUTER_DELETE_BUTTON = "حذف"
SETTINGS_OPENROUTER_INSTRUCTIONS_LABEL = "تعليمات الصياغة"
SETTINGS_OPENROUTER_TEST_BUTTON = "اختبار الاتصال"
SETTINGS_OPENROUTER_TEST_RUNNING = "جارٍ الاختبار…"
SETTINGS_OPENROUTER_TEST_SUCCESS = "الاتصال ناجح"
SETTINGS_OPENROUTER_TEST_FAILURE_PREFIX = "فشل الاختبار: "


def openrouter_test_failure(error: str) -> str:
    """error is a technical (Latin/English) message from openrouter.py -- isolate it per the mixed-text rule."""
    return SETTINGS_OPENROUTER_TEST_FAILURE_PREFIX + isolate_ltr(error)


# ---- live dictation (Phase 5) ---------------------------------------------

# Floating recording indicator (ui/indicator.py). No text is shown during
# the recording state itself -- just the live waveform, per README_AR.md
# ("مؤشر صغير جدًا... موجة حقيقية"). Text only appears in the processing/
# error states below.
INDICATOR_PROCESSING = "جارٍ المعالجة…"
INDICATOR_LOADING_MODEL = "جارٍ تحميل النموذج…"
INDICATOR_ERROR_MIC = "تعذر الوصول إلى الميكروفون"
INDICATOR_ERROR_GENERIC = "تعذّر الإملاء"

# Tray menu toggle -- "Enable dictation" (checkable, default on per the task spec).
TRAY_DICTATION_TOGGLE = "تفعيل الإملاء"
