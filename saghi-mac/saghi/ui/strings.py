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


def version_label(version: str) -> str:
    if version == "0.0.0":  # source checkout, see saghi/__init__.py
        return "نسخة التطوير"
    return f"الإصدار {isolate_ltr(version)}"


CANCEL = "إلغاء"
DELETE = "حذف"

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

PAGE_HISTORY_SUBTITLE = "كل ما كتبته بصوتك، محفوظ على جهازك فقط"
PAGE_FILEJOB_SUBTITLE = "حوّل تسجيلًا صوتيًا أو مقطع فيديو طويلًا إلى نص"
PAGE_SETTINGS_SUBTITLE = "كل التغييرات تُحفظ مباشرة"

# ---- history page -------------------------------------------------------

HISTORY_SEARCH_PLACEHOLDER = "ابحث في السجل…"
HISTORY_REFRESH = "تحديث"
HISTORY_COPY_BUTTON = "نسخ النص"
HISTORY_RAW_TOGGLE = "النص الخام"
HISTORY_EMPTY = "لا يوجد سجل بعد"
HISTORY_EMPTY_HINT = "اضغط الاختصار مطوّلًا وتكلّم، وسيظهر ما تكتبه هنا."
HISTORY_NO_RESULTS = "لا توجد نتائج مطابقة"
HISTORY_DELETE_BUTTON = "حذف"
HISTORY_EXPORT_BUTTON = "تصدير…"
HISTORY_EXPORT_FILTER = "Text (*.txt)"
HISTORY_CONFIRM_DELETE_TITLE = "حذف هذا النص؟"
HISTORY_CONFIRM_DELETE_TEXT = "سيُحذف من السجل نهائيًا ولا يمكن استرجاعه."

HISTORY_SOURCE_LABELS = {
    "dictation": "إملاء",
    "filejob": "تفريغ ملف",
    "api": isolate_ltr("API"),
    "cli": "سطر الأوامر",
}


def history_source_label(source: str) -> str:
    return HISTORY_SOURCE_LABELS.get(source or "", isolate_ltr(source or "—"))


def history_meta(entry: dict) -> str:
    """One line of details under an entry: source · duration · language · cleanup."""
    parts = [history_source_label(entry.get("source", ""))]
    duration = entry.get("duration_s")
    if isinstance(duration, (int, float)) and duration > 0:
        parts.append(f"المدة {_format_duration(duration)}")
    lang = LANGUAGE_LABELS.get(entry.get("language", ""))
    if lang:
        parts.append(lang)
    cleanup = CLEANUP_LEVEL_LABELS.get(entry.get("cleanup_level", ""))
    if cleanup:
        parts.append(f"التنظيف: {cleanup}")
    return "  ·  ".join(parts)

# ---- file transcription page ---------------------------------------------

FILEJOB_PICK_BUTTON = "اختيار ملف"
FILEJOB_DROP_TITLE = "اسحب ملف صوت أو فيديو إلى هنا"
FILEJOB_DROP_HINT = "أو اختره من جهازك"
FILEJOB_FORMATS = isolate_ltr("WAV · MP3 · M4A · AAC · FLAC · OGG · MP4")
FILEJOB_CHANGE_FILE = "اختيار ملف آخر"
FILEJOB_OPTIONS_TITLE = "خيارات التفريغ"
FILEJOB_PROGRESS_TITLE = "التقدّم"
FILEJOB_RESULT_TITLE = "النص"
FILEJOB_TIMESTAMPS_HINT = "يضيف وقت بداية كل مقطع إلى النص"
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


def filejob_file_size(size_bytes: int) -> str:
    """A human file size, e.g. '178.7 MB' (isolated: Latin digits + unit inside RTL text)."""
    if size_bytes >= 1024 ** 3:
        size = f"{size_bytes / 1024 ** 3:.1f} GB"
    else:
        size = f"{max(size_bytes, 0) / 1024 ** 2:.1f} MB"
    return isolate_ltr(size)


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

SETTINGS_SOUND_LABEL = "صوت التنبيه"
SETTINGS_GLASS_LABEL = "الشريط الجانبي الزجاجي"
SETTINGS_WAVEFORM_CUSTOM_COLOR = "لون مخصص…"

SETTINGS_SECTION_DICTATION = "الإملاء"
SETTINGS_SECTION_MIC = "الميكروفون"
SETTINGS_SECTION_INDICATOR = "مؤشر التسجيل"
SETTINGS_SECTION_GENERAL = "عام"
SETTINGS_SECTION_HISTORY = "السجل"
SETTINGS_SECTION_AI = "إعادة الصياغة بالذكاء الاصطناعي"
SETTINGS_SECTION_UPDATES = "التحديثات"

SETTINGS_HOTKEY_HINT = "اضغطه مطوّلًا وتكلّم، ثم اتركه ليُكتب النص. زر Esc يلغي التسجيل."
SETTINGS_LANGUAGE_HINT = "اللغة التي تتكلم بها"
SETTINGS_CLEANUP_HINT = "حذف كلمات التردد مثل «اممم» و«آه» من النص"
SETTINGS_AUTOPASTE_HINT = "يُلصق النص مباشرة حيث تكتب، ويبقى أيضًا في الحافظة"
SETTINGS_SOUND_HINT = "صوت خفيف عند بدء التسجيل وعند انتهاء الكتابة"
SETTINGS_MIC_HINT = "الجهاز الذي يُسجَّل منه صوتك"
SETTINGS_SAVE_RECORDINGS_HINT = "يحفظ ملف الصوت لكل إملاء على جهازك"
SETTINGS_WAVEFORM_STYLE_HINT = "شكل الموجة في المؤشر الصغير أسفل الشاشة أثناء التسجيل"
SETTINGS_LAUNCH_AT_LOGIN_HINT = "يعمل صاغي في الخلفية فور تسجيل الدخول إلى الجهاز"
SETTINGS_GLASS_HINT = "مظهر macOS الشفاف. يُطبَّق عند إعادة فتح صاغي"
SETTINGS_HISTORY_RETENTION_LABEL = "مدة الاحتفاظ بالسجل"
SETTINGS_HISTORY_RETENTION_HINT = "تُحذف النصوص الأقدم من هذه المدة تلقائيًا"
SETTINGS_HISTORY_CLEAR_LABEL = "مسح السجل"
SETTINGS_HISTORY_CLEAR_BUTTON = "مسح الكل…"
SETTINGS_HISTORY_CLEAR_CONFIRM_TITLE = "مسح السجل كله؟"
SETTINGS_HISTORY_CLEAR_CONFIRM_TEXT = "ستُحذف كل النصوص المحفوظة نهائيًا ولا يمكن استرجاعها."
SETTINGS_OPENROUTER_ENABLED_HINT = f"بعد التفريغ يُرسَل النص إلى {isolate_ltr('OpenRouter')} لإعادة صياغته، ويحتاج اتصالًا بالإنترنت"
SETTINGS_VERSION_LABEL = "الإصدار الحالي"
SETTINGS_CHECK_UPDATES_BUTTON = "التحقق من التحديثات"
SETTINGS_AUTO_UPDATE_LABEL = "التحقق من التحديثات تلقائيًا"
SETTINGS_AUTO_UPDATE_HINT = "مرة في اليوم، وينبّهك فقط دون أن يثبّت شيئًا"

HISTORY_RETENTION_LABELS = {
    0: "للأبد",
    7: f"{isolate_ltr('7')} أيام",
    30: f"{isolate_ltr('30')} يومًا",
    90: f"{isolate_ltr('90')} يومًا",
}


SETTINGS_HISTORY_PRUNE_CONFIRM_TITLE = "حذف النصوص الأقدم؟"


def history_prune_confirm_text(count: int) -> str:
    return f"يوجد {isolate_ltr(str(count))} من النصوص أقدم من هذه المدة، وستُحذف الآن نهائيًا."


def history_count_text(count: int) -> str:
    return f"عدد النصوص المحفوظة: {isolate_ltr(str(count))}"


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
    "ctrl+cmd": isolate_ltr("Control ⌃ + Command ⌘"),
    "alt+cmd": isolate_ltr("Option ⌥ + Command ⌘"),
    "shift+cmd": isolate_ltr("Shift ⇧ + Command ⌘"),
    "ctrl+alt": isolate_ltr("Control ⌃ + Option ⌥"),
    "ctrl+shift": isolate_ltr("Control ⌃ + Shift ⇧"),
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


# ---- updates (ui/update_dialog.py, tray) -------------------------------------

UPDATE_TITLE = "تحديث صاغي"
UPDATE_CHECKING = "جارٍ البحث عن إصدار جديد…"
UPDATE_UP_TO_DATE = "لديك أحدث إصدار"
UPDATE_NOW = "تحديث الآن"
UPDATE_LATER = "لاحقًا"
UPDATE_CLOSE = "إغلاق"
UPDATE_RETRY = "إعادة المحاولة"
UPDATE_DOWNLOADING = "جارٍ تنزيل التحديث والتحقق منه…"
UPDATE_INSTALLING = "جارٍ التثبيت…"
UPDATE_DONE_LIGHT = "تم تثبيت التحديث. أعد تشغيل صاغي ليبدأ الإصدار الجديد."
UPDATE_RESTART = "إعادة تشغيل صاغي"
UPDATE_NEEDS_FULL = (
    "هذا الإصدار يغيّر مكتبات التطبيق، فيحتاج المثبّت الكامل (تنزيل حوالي 4 GB). "
    "سيُغلق صاغي ويُفتح المثبّت في Terminal ليكمل التحديث تلقائيًا."
)
UPDATE_RUN_INSTALLER = "إغلاق صاغي وتشغيل المثبّت"
UPDATE_ERROR_PREFIX = "تعذّر التحديث: "
UPDATE_NOT_INSTALLED = "التحديث من داخل التطبيق يعمل في النسخة المثبّتة فقط. نزّل الإصدار الجديد من صفحة الإصدارات."
UPDATE_RELEASE_NOTES = "ما الجديد"
UPDATE_OPEN_PAGE = "صفحة الإصدار"

TRAY_CHECK_UPDATES = "التحقق من التحديثات…"
TRAY_UPDATE_AVAILABLE_TITLE = "يتوفر تحديث لصاغي"


def update_available(version: str) -> str:
    return f"يتوفر إصدار جديد: {isolate_ltr(version)}"


def update_current(version: str) -> str:
    return f"إصدارك الحالي: {isolate_ltr(version)}"


def update_error(message: str) -> str:
    return UPDATE_ERROR_PREFIX + isolate_ltr(message)


def tray_update_available_message(version: str) -> str:
    return f"الإصدار {isolate_ltr(version)} جاهز. افتح الإعدادات للتحديث."
