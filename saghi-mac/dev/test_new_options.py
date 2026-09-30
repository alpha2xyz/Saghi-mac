#!/usr/bin/env python3
"""
Tests for the options added with the macOS-style redesign -- no network, no
model, no real LaunchAgents dir, no real data dir:

  1. settings.py: new fields, defaults, and coercion of bad values.
  2. history.py: delete_entry / clear_all / prune_older_than / count.
  3. login_item.py: the LaunchAgent plist is written/removed under a
     scratch HOME, with the right program and environment.
  4. updater.py: version compare, picking the latest release, SHA256SUMS
     parsing, checksum refusal, prepare() choosing light vs full (with a
     fake downloader), and apply_light() swapping + rolling back.
  5. GUI: history page delete/export, settings page retention + clear-all,
     ToggleSwitch behaving like the checkbox it replaces.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_new_options.py

Plain assert-based, no pytest -- same style as the other dev/ tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SCRATCH = Path(tempfile.mkdtemp(prefix="saghi-test-new-options-"))
os.environ["SAGHI_DATA_DIR"] = str(SCRATCH / "data")
os.environ["HOME"] = str(SCRATCH / "home")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SAGHI_KEYCHAIN_SERVICE", "Saghi-test-new-options")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from saghi import history, login_item, settings, updater  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


# ---- 1. settings -------------------------------------------------------------

print("--- 1. settings: new fields + coercion ---")

d = settings.Settings()
check(d.sound_feedback is True, "sound_feedback defaults on")
check(d.history_retention_days == 0, "history retention defaults to keep-forever")
check(d.auto_check_updates is False, "automatic update checks default off (app stays offline)")
check(d.glass_effect is True, "glass sidebar defaults on")

bad = {
    "hotkey": "fn+cmd",
    "history_retention_days": 12,
    "sound_feedback": "yes",
    "auto_check_updates": 1,
    "glass_effect": None,
    "last_update_check": 5,
}
c = settings._coerce(bad)
check(c.hotkey == "ctrl+cmd", "an unknown hotkey falls back to the default")
check(c.history_retention_days == 0, "a retention period that is not offered falls back to 0")
check(c.sound_feedback is True and c.auto_check_updates is False and c.glass_effect is True, "non-bool switches fall back to defaults")
check(c.last_update_check == "", "a non-string last_update_check falls back to ''")
check(settings._coerce({"history_retention_days": True}).history_retention_days == 0, "True is not accepted as a number of days")
check(settings._coerce({"history_retention_days": 30.0}).history_retention_days == 30, "30.0 is read as 30 days")
for combo in settings.VALID_HOTKEYS:
    check(settings._coerce({"hotkey": combo}).hotkey == combo, f"hotkey preset {combo} is kept")

from saghi import hotkey  # noqa: E402

check(set(hotkey.COMBOS) == set(settings.VALID_HOTKEYS), "hotkey.COMBOS and settings.VALID_HOTKEYS list the same presets")


# ---- 2. history -----------------------------------------------------------------

print("--- 2. history: delete / clear / prune ---")


def _add(text: str) -> dict:
    return history.add_entry(
        source="dictation", language="ar", cleanup_level="light",
        duration_s=1.0, inference_s=0.5, raw_text=text, text=text,
    )


a, b, c3 = _add("أول"), _add("ثاني"), _add("ثالث")
check(history.count() == 3, "count() sees 3 entries")
check(history.delete_entry(b["id"]) is True, "delete_entry removes an existing entry")
check(history.delete_entry(b["id"]) is False, "deleting it again reports nothing removed")
check([e["text"] for e in history.search()] == ["ثالث", "أول"], "the other entries are untouched")

now = datetime.now(timezone.utc)
check(history.prune_older_than(0) == 0, "retention 0 (forever) prunes nothing")
check(history.prune_older_than(7, now=now) == 0, "fresh entries survive a 7-day retention")
check(history.prune_older_than(7, now=now + timedelta(days=8)) == 2, "entries older than 7 days are pruned")
check(history.count() == 0, "history is empty after pruning")
_add("واحد")
_add("اثنان")
check(history.clear_all() == 2, "clear_all removes everything and reports how many")
check(history.count() == 0, "count() is 0 after clear_all")


# ---- 3. login item -----------------------------------------------------------------

print("--- 3. launch at login (LaunchAgent under a scratch HOME) ---")

check(login_item.plist_path().is_relative_to(SCRATCH), "the plist path is under the scratch HOME, never the real one")
check(login_item.is_enabled() is False, "not enabled at first")
fake_exe = "/Users/x/Applications/Saghi.app/Contents/Resources/venv/bin/python3.12"
plist = login_item.build_plist(executable=fake_exe, model_dir="/models/m")
check(plist["ProgramArguments"] == [fake_exe, "-m", "saghi.ui.app"], "runs `python -m saghi.ui.app` with the app's own Python")
check(plist["RunAtLoad"] is True and plist["KeepAlive"] is False, "starts at login once, is not kept alive")
check(plist["EnvironmentVariables"]["SAGHI_MODEL_DIR"] == "/models/m", "passes the model dir")
check(plist["WorkingDirectory"] == "/Users/x/Applications/Saghi.app/Contents/Resources", "works from the app's Resources dir")
check(plist["Label"] == "io.github.alpha2xyz.saghi-mac", "same label as the installer's LaunchAgent")

check(login_item.set_enabled(True, model_dir="/models/m") is True, "set_enabled(True) succeeds")
check(login_item.is_enabled() is True, "the plist now exists")
with open(login_item.plist_path(), "rb") as f:
    check(plistlib.load(f)["Label"] == login_item.LABEL, "the written file is a valid plist")
check(login_item.set_enabled(False) is True and login_item.is_enabled() is False, "set_enabled(False) removes it")
check(login_item.set_enabled(False) is True, "disabling twice is harmless")


# ---- 4. updater --------------------------------------------------------------------

print("--- 4. updater (no network) ---")

check(updater.is_newer("0.2.0", "0.1.0"), "0.2.0 is newer than 0.1.0")
check(updater.is_newer("0.10.0", "0.9.3"), "versions compare numerically, not as text")
check(not updater.is_newer("0.2", "0.2.0"), "0.2 and 0.2.0 are the same version")
check(not updater.is_newer("v0.1.0", "0.1.0"), "a leading v is ignored")

releases = [
    {"tag_name": "model-v1", "assets": []},
    {"tag_name": "v0.3.0-rc1", "prerelease": True, "assets": []},
    {"tag_name": "v0.1.0", "assets": []},
    {"tag_name": "v0.2.1", "body": "notes", "html_url": "https://example/rel", "assets": [
        {"name": "SHA256SUMS", "browser_download_url": "https://example/SHA256SUMS"},
    ]},
    {"tag_name": "v0.9.0", "draft": True, "assets": []},
]
latest = updater.pick_latest(releases)
check(latest is not None and latest.tag == "v0.2.1", "pick_latest skips model-v1, drafts and pre-releases, and takes the highest version")
check(latest.assets["SHA256SUMS"] == "https://example/SHA256SUMS", "asset download URLs are kept by name")
check(updater.pick_latest([{"tag_name": "model-v1"}]) is None, "no app release -> None")

with patch("saghi.updater.fetch_releases", return_value=releases):
    r = updater.check(current="0.2.0")
    check(r.update_available and r.latest.version == "0.2.1", "check() reports 0.2.1 as an update for 0.2.0")
    r = updater.check(current="0.2.1")
    check(not r.update_available, "check() reports no update when already on the latest")

h = "a" * 64
sums = updater.parse_sums(f"{h}  Saghi-mac-app-v0.2.1.zip\n{'b' * 64} *Install-Saghi-mac.command\nnot a line\n")
check(sums == {"Saghi-mac-app-v0.2.1.zip": h, "Install-Saghi-mac.command": "b" * 64}, "parse_sums reads both checksum line styles")

work = SCRATCH / "upd"
work.mkdir()
f = work / "x.bin"
f.write_bytes(b"hello")
good = hashlib.sha256(b"hello").hexdigest()
updater.verify_file(f, "x.bin", {"x.bin": good})
check(True, "verify_file accepts a matching checksum")
for bad_sums, why in (({"x.bin": "0" * 64}, "a wrong checksum"), ({}, "a missing checksum")):
    try:
        updater.verify_file(f, "x.bin", bad_sums)
        check(False, f"verify_file must refuse {why}")
    except updater.UpdateError:
        check(True, f"verify_file refuses {why}")


def _make_light_zip(path: Path, req_sha: str, marker: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("saghi/__init__.py", f"MARKER = {marker!r}\n")
        zf.writestr("saghi/_version.py", f'__version__ = "0.2.1"\nREQUIREMENTS_SHA256 = "{req_sha}"\n')
        zf.writestr("requirements-arm64.txt", "x==1\n")


def _fake_release_files(req_sha: str, marker: str = "new") -> dict:
    """name -> bytes for a fake release."""
    tmp = SCRATCH / f"fake-{marker}-{req_sha[:4]}.zip"
    _make_light_zip(tmp, req_sha, marker)
    files = {
        "Saghi-mac-app-v0.2.1.zip": tmp.read_bytes(),
        "Install-Saghi-mac.command": b"#!/bin/bash\necho install\n",
    }
    files["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items()
    ).encode()
    return files


def _release_for(files: dict) -> updater.ReleaseInfo:
    return updater.ReleaseInfo(
        tag="v0.2.1", version="0.2.1", notes="", html_url="",
        assets={name: f"https://example/{name}" for name in files},
    )


def _fake_download(files: dict):
    def download(url, dest, on_progress=None):
        data = files[url.rsplit("/", 1)[1]]
        Path(dest).write_bytes(data)
        if on_progress:
            on_progress(len(data), len(data))
        return Path(dest)

    return download


same_req = "c" * 64
files = _fake_release_files(same_req)
with patch("saghi.updater.download", side_effect=_fake_download(files)):
    prepared = updater.prepare(_release_for(files), installed_requirements_sha=same_req, workdir=work / "p1")
check(prepared.kind == "light", "same libraries -> a light (code-only) update")
check((prepared.package_dir / "__init__.py").read_text().startswith("MARKER = 'new'"), "the light update's saghi/ was extracted")

with patch("saghi.updater.download", side_effect=_fake_download(files)):
    prepared_full = updater.prepare(_release_for(files), installed_requirements_sha="d" * 64, workdir=work / "p2")
check(prepared_full.kind == "full", "changed libraries -> the full installer")
check(prepared_full.installer.read_bytes().startswith(b"#!/bin/bash"), "the verified installer was downloaded")
check(os.access(prepared_full.installer, os.X_OK), "the installer is executable")

tampered = dict(files)
tampered["Saghi-mac-app-v0.2.1.zip"] = files["Saghi-mac-app-v0.2.1.zip"] + b"junk"
try:
    with patch("saghi.updater.download", side_effect=_fake_download(tampered)):
        updater.prepare(_release_for(tampered), installed_requirements_sha=same_req, workdir=work / "p3")
    check(False, "a damaged update must be refused")
except updater.UpdateError:
    check(True, "a damaged (checksum-mismatch) update is refused")
check(not (work / "p3").exists(), "the work folder of a failed update is cleaned up")

# apply_light on a scratch install
install = SCRATCH / "install" / "app"
(install / "saghi").mkdir(parents=True)
(install / "saghi" / "__init__.py").write_text("MARKER = 'old'\n")
target = install / "saghi"
backup = updater.apply_light(prepared, target=target)
check((target / "__init__.py").read_text().startswith("MARKER = 'new'"), "apply_light installs the new saghi/")
check((backup / "__init__.py").read_text().startswith("MARKER = 'old'"), "the previous saghi/ is kept as a backup")
check(not (install / "saghi.incoming").exists(), "no staging folder is left behind")

# rollback: make the final rename fail -> the old folder must come back
(install / "saghi" / "__init__.py").write_text("MARKER = 'current'\n")
with patch("saghi.updater.download", side_effect=_fake_download(files)):
    prepared2 = updater.prepare(_release_for(files), installed_requirements_sha=same_req, workdir=work / "p4")
real_replace = os.replace
calls = {"n": 0}


def flaky_replace(src, dst):
    calls["n"] += 1
    if calls["n"] == 2:  # 1st = current -> backup, 2nd = staging -> current
        raise OSError("disk full")
    return real_replace(src, dst)


try:
    with patch("saghi.updater.os.replace", side_effect=flaky_replace):
        updater.apply_light(prepared2, target=target)
    check(False, "apply_light should report the failed swap")
except updater.UpdateError:
    check(True, "a failed swap is reported as an UpdateError")
check((target / "__init__.py").read_text().startswith("MARKER = 'current'"), "a failed swap puts the running version back")

try:
    bad_zip = work / "evil.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../escape.txt", "x")
    updater._safe_extract(bad_zip, work / "evil-out")
    check(False, "an archive with ../ paths must be refused")
except updater.UpdateError:
    check(True, "an archive with ../ paths is refused")


# ---- 5. GUI ----------------------------------------------------------------------------

print("--- 5. GUI: history actions, settings history card, switches ---")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

from saghi.ui import strings, widgets  # noqa: E402
from saghi.ui.history_page import HistoryPage  # noqa: E402
from saghi.ui.settings_page import SettingsPage  # noqa: E402

sw = widgets.ToggleSwitch()
seen = []
sw.toggled.connect(seen.append)
sw.setChecked(True)
check(sw.isChecked() is True and seen == [True], "ToggleSwitch.setChecked + toggled behave like a checkbox")
sw.click()
check(sw.isChecked() is False and seen == [True, False], "clicking the switch flips it")

_add("نص للحذف")
_add("نص للتصدير")
hp = HistoryPage()
hp.show()
app.processEvents()
check(hp.results_list.count() == 2, "history page lists 2 entries")
out = hp.export_selected(str(SCRATCH / "export"))
check(out is not None and out.suffix == ".txt", "export adds the .txt extension")
check(out.read_text(encoding="utf-8").strip() == "نص للتصدير", "export writes the shown text as UTF-8")
check(hp.delete_selected(confirm=False) is True, "delete_selected removes the entry")
check(hp.results_list.count() == 1 and history.count() == 1, "the list and the database both drop it")
hp.delete_selected(confirm=False)
check(hp.results_list.count() == 0, "the list is empty after deleting the last entry")
check(hp._stack.currentIndex() == 1, "an empty history shows the empty state")
check(hp.delete_btn.isEnabled() is False, "actions are disabled with nothing selected")
hp.search_box.setText("لا يوجد")
check(hp.empty_title.text() == strings.HISTORY_NO_RESULTS, "an empty search says 'no results' rather than 'no history'")

sm = settings.SettingsManager()
page = SettingsPage(sm)
cleared = []
page.history_cleared.connect(lambda: cleared.append(True))
_add("واحد")
_add("اثنان")
page.refresh_history_count()
check(page.history_count_label.text() == strings.history_count_text(2), "the history card shows how many entries are saved")
check(page.history_clear_btn.isEnabled(), "clear-all is enabled when there is history")
page.clear_history()
check(history.count() == 0 and cleared == [True], "clear_history empties the database and tells the History page")
check(page.history_clear_btn.isEnabled() is False, "clear-all is disabled once history is empty")

idx = page.history_retention_combo.findData(30)
page.history_retention_combo.setCurrentIndex(idx)
check(sm.current.history_retention_days == 30, "choosing a retention period saves it")
check(json.loads(settings.settings_path().read_text())["history_retention_days"] == 30, "…to settings.json")

# Shortening retention when old entries exist asks first; "no" keeps everything.
old_entry = _add("قديم")
with history._connect() as conn:
    conn.execute("UPDATE history SET created_at = ? WHERE id = ?", ("2020-01-01T00:00:00.000000+00:00", old_entry["id"]))
check(history.count_older_than(7) == 1, "count_older_than sees the old entry")
with patch("saghi.ui.widgets.confirm_destructive", return_value=False):
    page.history_retention_combo.setCurrentIndex(page.history_retention_combo.findData(7))
check(sm.current.history_retention_days == 30 and history.count() == 1, "declining keeps the old setting and the entry")
check(page.history_retention_combo.currentData() == 30, "declining puts the previous choice back in the menu")
with patch("saghi.ui.widgets.confirm_destructive", return_value=True):
    page.history_retention_combo.setCurrentIndex(page.history_retention_combo.findData(7))
check(sm.current.history_retention_days == 7 and history.count() == 0, "confirming saves 7 days and prunes the old entry")

page.sound_check.setChecked(False)
check(sm.current.sound_feedback is False, "the sound switch saves")
page.hotkey_combo.setCurrentIndex(page.hotkey_combo.findData("ctrl+shift"))
check(sm.current.hotkey == "ctrl+shift", "the new hotkey presets are selectable")

with patch("saghi.login_item.is_supported", return_value=True):
    page.launch_check.setChecked(True)
    check(sm.current.launch_at_login is True and login_item.is_enabled(), "turning on launch-at-login writes the LaunchAgent")
    page.launch_check.setChecked(False)
    check(sm.current.launch_at_login is False and not login_item.is_enabled(), "turning it off removes it")

page._set_waveform_color("#123456")
check(sm.current.waveform_color == "#123456", "a custom waveform colour saves")
check(not page.custom_color_dot.isHidden() and page.custom_color_dot.isChecked(), "a custom colour gets its own, selected swatch")
page._set_waveform_color(page.color_dots[1].color)
check(page.color_dots[1].isChecked() and page.custom_color_dot.isHidden(), "picking a preset checks its swatch and hides the custom one")

page.openrouter_enabled_check.setChecked(False)
check(page.openrouter_model_edit.isEnabled() is False, "AI fields are disabled while rephrasing is off")
page.openrouter_enabled_check.setChecked(True)
check(page.openrouter_model_edit.isEnabled() is True, "…and enabled again when it is on")

requested = []
page.check_updates_requested.connect(lambda: requested.append(True))
page.check_updates_btn.click()
check(requested == [True], "the 'check for updates' button asks the app to open the update dialog")

from saghi.ui.update_dialog import UpdateController  # noqa: E402

ctl = UpdateController(sm, quit_app=lambda: None)
sm.update(auto_check_updates=False)
check(ctl.auto_check_due() is False, "no automatic check while the switch is off")
sm.update(auto_check_updates=True, last_update_check="")
check(ctl.auto_check_due() is True, "an automatic check is due when never checked")
sm.update(last_update_check=datetime.now(timezone.utc).isoformat())
check(ctl.auto_check_due() is False, "not due again within a day")
check(ctl.auto_check_due(now=datetime.now(timezone.utc) + timedelta(days=1, minutes=1)) is True, "due again after a day")

# Floating pill + UI font options.
check(settings._coerce({"ui_font": "x"}).ui_font == "amiri", "an unknown ui_font falls back to amiri")
check(settings._coerce({"floating_pill_mode": "x"}).floating_pill_mode == "always", "an unknown pill mode falls back to always")
for bad_pos in ("x", [1], [1, 2, 3], [True, 2], ["a", "b"], [float("nan"), 1], {"x": 1, "y": 2}):
    check(settings._coerce({"floating_pill_pos": bad_pos}).floating_pill_pos is None, f"a bad floating_pill_pos {bad_pos!r} becomes None")
check(settings._coerce({"floating_pill_pos": [10.4, 20]}).floating_pill_pos == [10, 20], "a good floating_pill_pos is kept as [int, int]")

check(page.floating_pill_combo.count() == len(settings.VALID_PILL_MODES), "the pill combo offers every mode")
check(
    [page.floating_pill_combo.itemData(i) for i in range(page.floating_pill_combo.count())] == list(settings.VALID_PILL_MODES),
    "the pill combo lists the modes in VALID_PILL_MODES order",
)
check(page.floating_pill_combo.currentData() == sm.current.floating_pill_mode == "always", "the pill combo starts on the saved mode")
page.floating_pill_combo.setCurrentIndex(page.floating_pill_combo.findData("active"))
check(sm.current.floating_pill_mode == "active", "choosing a pill mode saves it")
check(json.loads(settings.settings_path().read_text())["floating_pill_mode"] == "active", "…to settings.json")
_notified = []
sm.on_change(lambda s: _notified.append(s.floating_pill_mode))
sm.update(floating_pill_mode="always")
check(page.floating_pill_combo.currentData() == "always", "an outside change of the pill mode (menu-bar menu) updates the combo")
check(_notified == ["always"], "…without the page writing anything back (exactly one change notification)")

check(page.pill_reset_btn.isEnabled() is False, "the pill reset button is disabled while no position is saved")
sm.update(floating_pill_pos=[120, 340])
check(page.pill_reset_btn.isEnabled() is True, "the pill reset button is enabled once the pill has a saved position")
page.pill_reset_btn.click()
check(sm.current.floating_pill_pos is None, "clicking the reset button clears the saved position")
check(page.pill_reset_btn.isEnabled() is False, "…and disables the button again")
check(json.loads(settings.settings_path().read_text())["floating_pill_pos"] is None, "…in settings.json too")

page.floating_pill_combo.setCurrentIndex(page.floating_pill_combo.findData("active"))
sm.update(floating_pill_pos=[5, 6])
check(page.floating_pill_combo.currentData() == "active", "a position change leaves the pill mode selection alone")
sm.update(floating_pill_mode="always", floating_pill_pos=None)

sm2 = settings.SettingsManager()
sm2.update(floating_pill_mode="active", floating_pill_pos=[7, 8], ui_font="system")
page2 = SettingsPage(sm2)
check(page2.floating_pill_combo.currentData() == "active", "a new page starts on the saved pill mode")
check(page2.pill_reset_btn.isEnabled() is True, "…with the reset button enabled for the saved position")
check(page2.ui_font_combo.currentData() == "system", "…and the saved UI font selected")

check(page.ui_font_combo.count() == len(settings.VALID_UI_FONTS), "the UI font combo offers every font")
check(
    [page.ui_font_combo.itemData(i) for i in range(page.ui_font_combo.count())] == list(settings.VALID_UI_FONTS),
    "the UI font combo lists the fonts in VALID_UI_FONTS order",
)
check(page.ui_font_combo.currentData() == "amiri", "the UI font combo starts on the default font")
page.ui_font_combo.setCurrentIndex(page.ui_font_combo.findData("system"))
check(sm.current.ui_font == "system", "choosing the system font saves ui_font")
check(json.loads(settings.settings_path().read_text())["ui_font"] == "system", "…to settings.json")
page.ui_font_combo.setCurrentIndex(page.ui_font_combo.findData("amiri"))
check(sm.current.ui_font == "amiri", "choosing Amiri saves ui_font")

# A page deleted while its listener is still registered must not raise from it.
import shiboken6  # noqa: E402

shiboken6.delete(page2)
try:
    page2._on_settings_changed(sm2.current)
    check(True, "the settings listener tolerates a deleted page")
except RuntimeError:
    check(False, "the settings listener must tolerate a deleted page")
sm2.update(floating_pill_mode="always")
check(sm2.current.floating_pill_mode == "always", "later settings changes still work after the page is deleted")

# Amiri everywhere except macOS's fixed-height native controls (they would clip it).
from saghi.ui import theme  # noqa: E402

with patch.object(sys, "platform", "darwin"):
    check(theme.apply_ui_font(app, "amiri") == "amiri", "Amiri applies")
check(QApplication.font().family() == "Amiri", "the app font is Amiri")
for cls in ("QPushButton", "QComboBox", "QLineEdit"):
    check(QApplication.font(cls).family() != "Amiri", f"on macOS {cls} keeps the system font (fixed native height)")
check(QApplication.font("QLabel").family() == "Amiri", "labels stay Amiri")
check(theme.apply_ui_font(app, "system") == "system", "switching back to the system font")
check(QApplication.font("QPushButton").family() == QApplication.font().family(), "...clears the per-class fonts")
check(not strings.SETTINGS_UI_FONT_HINT.lstrip("\u2066")[0].isascii(),
      "the font hint starts with Arabic, so it right-aligns under its title")

print(f"\nALL PASSED ({_checks} checks)")
