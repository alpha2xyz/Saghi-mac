<p align="center"><img src="saghi-mac/packaging/icon/saghi-icon-1024.png" width="160" alt="Saghi-mac icon"></p>

# Saghi-mac

Free Arabic voice typing for Apple-silicon Macs. It runs fully offline, on your Mac. Your voice never leaves the device.

Hold a hotkey, speak Arabic, let go. Saghi-mac turns your speech into clean text and pastes it where you were typing.

![Saghi-mac settings page](saghi-mac/dev/screenshots/page-settings.png)

## Requirements

- A Mac with Apple silicon (M1 or newer). Intel Macs are not supported.
- macOS 13 (Ventura) or newer.
- About 10 GB of free disk space (the model alone is about 4 GB).
- 8 GB of RAM or more is recommended.

## Install

1. Open the latest release: https://github.com/alpha2xyz/Saghi-mac/releases/latest
2. Download `Install-Saghi-mac.zip` from the release assets, then double-click it to unzip. You get `Install-Saghi-mac.command`.
3. Double-click it. The first time, macOS Gatekeeper will block it because it is not signed:
   - macOS 15 (Sequoia) or newer: double-click the file, then open System Settings, Privacy & Security, scroll down and click **Open Anyway**, then confirm.
   - macOS 13 or 14: Control-click the file, choose **Open**, then click **Open** again.
   - If macOS says the file "could not be executed because you do not have appropriate access privileges", open Terminal and run: `bash ~/Downloads/Install-Saghi-mac.command`
4. The installer downloads the app (about 4 GB), checks it, and installs Saghi to `~/Applications/Saghi.app`. It needs an internet connection only for this step. No admin password is needed.
5. It asks if you want Saghi to start at login (default: no).

To install a specific version, run the installer with `SAGHI_RELEASE_TAG=v0.1.0`.

## Permissions

macOS asks for these the first time you use each feature (System Settings, Privacy & Security):

| Permission | Why |
|---|---|
| Microphone | To record your voice. |
| Input Monitoring | To detect the global hotkey from any app. |
| Accessibility | To paste the text into the active app. |

Look for **Saghi** in each list and switch it on. If you see `python3.12` instead, switch that one on.

## Usage

1. Click into any text field: browser, Notes, WhatsApp, Word, anything.
2. Hold the hotkey. The default is **Control + Command**. In Settings you can pick Option + Command, Shift + Command, Control + Option or Control + Shift instead.
3. Speak.
4. Release the keys. The text is cleaned (hesitation sounds removed) and pasted. It is also kept on the clipboard.
5. Press **Esc** before releasing to cancel a recording. Pressing any other key while holding the hotkey also cancels it (you were typing a shortcut, not dictating).

Open the **File transcription** page to transcribe audio or video files (WAV, MP3, M4A, AAC, FLAC, OGG, MP4). Long files are processed in safe chunks and progress is saved, so a stopped job resumes where it left off. You can export plain text, SRT or WebVTT.

The **History** page keeps what you dictated. You can search it, copy, export an entry as a text file, delete one entry, or clear everything from Settings. Settings can also delete entries older than 7, 30 or 90 days automatically.

### Status at a glance

- **Floating pill.** A small glass capsule stays on screen and shows what Saghi is doing: ready, listening (with your live waveform and a timer), processing, loading the model, an error, or dictation turned off. Drag it anywhere; it remembers the spot. Click it to open Saghi. Right-click it to move it to the bottom centre or just under the menu bar (notch), or to show it only while dictating. It never takes the focus away from the app you are typing in.
- **Menu-bar icon.** Saghi's icon in the menu bar changes with the same states (a red microphone while it listens). Its menu shows the current state and your hotkey.

The icons are Apple's own SF Symbols, drawn by macOS, and the interface uses the Amiri typeface (bundled; you can switch back to the system font in Settings).

Other options in **Settings**: a short sound when recording starts and ends, the recording indicator's style and colour, the floating pill (always visible or only while dictating), launch at login, the interface font, and the translucent macOS sidebar.

Your data lives in `~/Library/Application Support/Saghi/`. A local API listens on `127.0.0.1:17865` only, never on the network.

## Updating

From version 0.2.0 on, open **Settings**, then **Check for updates** (also in the menu-bar icon's menu). If a newer release exists, click **Update now**:

- Usually only Saghi's own code changed. The app downloads a small file (a few MB), checks it, installs it, and asks you to restart Saghi.
- If a release changes the app's libraries, Saghi quits and opens the full installer in Terminal, which updates everything by itself (about 4 GB download).

Every download is checked against the release's SHA256SUMS before it is used. Saghi only contacts GitHub when you click the button, or once a day if you turn on **Check for updates automatically** (off by default). It never installs an update without your click.

Version 0.1.0 has no update button: install 0.2.0 once with the installer, as in [Install](#install).

## Build from source

See [docs/BUILDING.md](docs/BUILDING.md).

## Credits

- **Saghi (صاغي)** is the original Windows app by **أسلس AI** in collaboration with **جندي**: https://asls-ai.auraspectrum.sa/saghi/
- The speech model is **cohere-transcribe-arabic-07-2026** by **Cohere Labs** (Apache 2.0): https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026
- Other open-source components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Saghi-mac is an independent, free, community Mac version. It is not the official app and is not affiliated with or endorsed by أسلس AI.**

## License

MIT. See [LICENSE](LICENSE). The model and third-party libraries keep their own licenses.

---

<div dir="rtl">

# Saghi-mac (صاغي لنظام Mac)

كتابة صوتية عربية مجانية لأجهزة Mac بمعالج Apple silicon. تعمل بالكامل دون إنترنت وعلى جهازك، وصوتك لا يغادر الجهاز.

اضغط الاختصار مطولًا، تكلّم بالعربية، ثم اترك الأزرار. يحوّل Saghi-mac كلامك إلى نص منظّف ويلصقه في مكان الكتابة.

## المتطلبات

- جهاز Mac بمعالج Apple silicon (M1 أو أحدث). أجهزة Intel غير مدعومة.
- نظام macOS 13 (Ventura) أو أحدث.
- حوالي 10 GB مساحة فارغة (النموذج وحده حوالي 4 GB).
- يُنصح بذاكرة 8 GB أو أكثر.

## التثبيت

1. افتح أحدث إصدار: https://github.com/alpha2xyz/Saghi-mac/releases/latest
2. نزّل الملف `Install-Saghi-mac.zip` من ملفات الإصدار، ثم انقر عليه مرتين لفك الضغط. ستحصل على الملف `Install-Saghi-mac.command`.
3. انقر عليه مرتين. في أول مرة سيمنعه macOS لأنه غير موقّع رقميًا:
   - macOS 15 (Sequoia) أو أحدث: انقر مرتين على الملف، ثم افتح System Settings ثم Privacy & Security، مرّر لأسفل واضغط **Open Anyway**، ثم أكّد.
   - macOS 13 أو 14: اضغط على الملف مع الضغط على Control، اختر **Open**، ثم اضغط **Open** مرة أخرى.
   - إذا ظهرت رسالة بأن الملف "could not be executed because you do not have appropriate access privileges"، افتح Terminal واكتب: `bash ~/Downloads/Install-Saghi-mac.command`
4. سينزّل المثبّت التطبيق (حوالي 4 GB)، ويتحقق منه، ثم يثبّت صاغي في `~/Applications/Saghi.app`. يحتاج الإنترنت في هذه الخطوة فقط، ولا يحتاج كلمة مرور المدير.
5. سيسألك إن كنت تريد تشغيل صاغي عند الدخول إلى الجهاز (الافتراضي: لا).

لتثبيت إصدار محدد شغّل المثبّت مع `SAGHI_RELEASE_TAG=v0.1.0`.

## الصلاحيات

يطلب macOS هذه الصلاحيات عند أول استخدام لكل ميزة (System Settings ثم Privacy & Security):

| الصلاحية | السبب |
|---|---|
| Microphone (الميكروفون) | تسجيل صوتك. |
| Input Monitoring | رصد الاختصار العام من أي تطبيق. |
| Accessibility | لصق النص في التطبيق النشط. |

ابحث عن **Saghi** في كل قائمة وفعّله. إذا ظهر `python3.12` بدلًا منه فعّل هذا.

## الاستخدام

1. اضغط داخل أي مكان للكتابة: المتصفح أو Notes أو WhatsApp أو Word أو غيرها.
2. اضغط مطولًا على الاختصار. الافتراضي هو **Control + Command**، ويمكنك اختيار Option + Command أو Shift + Command أو Control + Option أو Control + Shift من الإعدادات.
3. تكلّم.
4. اترك الأزرار. يُنظّف النص (تُحذف أصوات التردد) ثم يُلصق، ويبقى أيضًا في الحافظة.
5. اضغط **Esc** قبل ترك الأزرار لإلغاء التسجيل. الضغط على أي زر آخر أثناء الاختصار يلغيه أيضًا (لأنك تكتب اختصار لوحة مفاتيح، لا تُملي).

صفحة **تفريغ ملف** تفرّغ ملفات الصوت والفيديو (WAV وMP3 وM4A وAAC وFLAC وOGG وMP4). تُعالج الملفات الطويلة على أجزاء آمنة ويُحفظ التقدم، فإذا توقفت المهمة تكمل من حيث توقفت. يمكنك التصدير كنص عادي أو SRT أو WebVTT.

صفحة **السجل** تحفظ ما أمليته. يمكنك البحث فيه، ونسخ نص، وتصديره كملف نصي، وحذف نص واحد، أو مسح السجل كله من الإعدادات. ويمكن للإعدادات أيضًا حذف النصوص الأقدم من 7 أو 30 أو 90 يومًا تلقائيًا.

### الحالة بنظرة

- **التبويب العائم.** كبسولة زجاجية صغيرة تبقى على الشاشة وتبيّن حالة صاغي: جاهز، يستمع (مع موجة صوتك وعدّاد الوقت)، جارٍ المعالجة، جارٍ تحميل النموذج، خطأ، أو الإملاء متوقف. اسحبها لأي مكان وتتذكر مكانها. اضغطها لفتح صاغي. واضغطها بالزر الأيمن لنقلها أسفل الشاشة بالنص أو تحت شريط القوائم (النوتش)، أو لإظهارها أثناء الإملاء فقط. ولا تأخذ التركيز أبدًا من التطبيق الذي تكتب فيه.
- **أيقونة شريط القوائم.** أيقونة صاغي أعلى الشاشة تتغيّر بنفس الحالات (ميكروفون أحمر وهو يستمع)، وقائمتها تبيّن الحالة والاختصار.

الأيقونات هي أيقونات SF Symbols من أبل يرسمها macOS نفسه، والواجهة بخط Amiri (مضمّن في التطبيق، ويمكنك الرجوع لخط النظام من الإعدادات).

خيارات أخرى في **الإعدادات**: صوت خفيف عند بدء التسجيل وانتهائه، وشكل ولون مؤشر التسجيل، والتبويب العائم (دائمًا ظاهر أو أثناء الإملاء فقط)، والتشغيل عند الدخول إلى الجهاز، وخط الواجهة، والشريط الجانبي الزجاجي بطابع macOS.

بياناتك في `~/Library/Application Support/Saghi/`. واجهة API محلية تعمل على `127.0.0.1:17865` فقط، ولا تستمع لأي اتصال من الشبكة.

## التحديث

من الإصدار 0.2.0 وما بعده: افتح **الإعدادات** ثم **التحقق من التحديثات** (موجود أيضًا في قائمة أيقونة صاغي أعلى الشاشة). إذا وُجد إصدار أحدث اضغط **تحديث الآن**:

- في الغالب يتغير كود صاغي فقط، فينزّل التطبيق ملفًا صغيرًا (بضعة MB)، ويتحقق منه، ويثبّته، ثم يطلب إعادة تشغيل صاغي.
- إذا غيّر الإصدار مكتبات التطبيق، يُغلق صاغي ويفتح المثبّت الكامل في Terminal ليحدّث كل شيء تلقائيًا (تنزيل حوالي 4 GB).

كل ملف يُنزَّل يُتحقق منه بملف SHA256SUMS الخاص بالإصدار قبل استخدامه. لا يتصل صاغي بـ GitHub إلا عند ضغط الزر، أو مرة في اليوم إذا فعّلت **التحقق من التحديثات تلقائيًا** (مُطفأ افتراضيًا). ولا يثبّت أي تحديث دون موافقتك.

الإصدار 0.1.0 لا يحتوي زر التحديث: ثبّت الإصدار 0.2.0 مرة واحدة بالمثبّت كما في قسم التثبيت.

## البناء من المصدر

راجع [docs/BUILDING.md](docs/BUILDING.md).

## حقوق ونسبة

- **صاغي** هو تطبيق Windows الأصلي من **أسلس AI** بالتعاون مع **جندي**: https://asls-ai.auraspectrum.sa/saghi/
- نموذج التعرف على الكلام **cohere-transcribe-arabic-07-2026** من **Cohere Labs** (رخصة Apache 2.0): https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026
- المكونات المفتوحة الأخرى مذكورة في [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

**Saghi-mac نسخة Mac مستقلة ومجانية من المجتمع. ليست التطبيق الرسمي، وليست مرتبطة بأسلس AI أو معتمدة منها.**

## الرخصة

MIT. راجع [LICENSE](LICENSE). للنموذج والمكتبات الخارجية رخصها الخاصة.

</div>
