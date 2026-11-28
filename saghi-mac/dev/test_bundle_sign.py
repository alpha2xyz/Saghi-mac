"""Symlink fix + ad-hoc re-sign after an in-place update (codesign is mocked, runs on Linux)."""
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from saghi import bundle_sign  # noqa: E402

tmp = Path(tempfile.mkdtemp())
app = tmp / "Saghi.app"
rt = app / "Contents/Resources/python-runtime/bin"
vb = app / "Contents/Resources/venv/bin"
rt.mkdir(parents=True); vb.mkdir(parents=True)
(rt / "python3.12").write_text("py")
os.symlink(str(rt / "python3.12"), vb / "python3.12")          # absolute, inside the app
os.symlink("python3.12", vb / "python3")                       # already relative
(tmp / "outside").write_text("x")
os.symlink(str(tmp / "outside"), vb / "ext")                   # absolute, outside the app

fixed = bundle_sign.fix_absolute_symlinks(app)
assert fixed == [vb / "python3.12"], fixed
assert os.readlink(vb / "python3.12") == "../../python-runtime/bin/python3.12"
assert (vb / "python3.12").read_text() == "py"
assert os.readlink(vb / "python3") == "python3.12"
assert os.path.isabs(os.readlink(vb / "ext"))

# Fake codesign: logs calls; verify fails when FAIL_VERIFY exists.
log = tmp / "calls.log"
fake = tmp / "codesign"
fake.write_text(f'#!/bin/sh\necho "$@" >> {log}\n'
                f'case "$1" in --verify) [ -e {tmp}/FAIL_VERIFY ] && {{ echo bad >&2; exit 1; }} ;; esac\nexit 0\n')
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
bundle_sign.CODESIGN = str(fake)

assert bundle_sign.resign(app) is True
calls = log.read_text().splitlines()
assert calls == [f"--force -s - {app}", f"--verify --strict {app}"], calls

(tmp / "FAIL_VERIFY").write_text("")
assert bundle_sign.resign(app) is False

bundle_sign.CODESIGN = str(tmp / "missing-codesign")
assert bundle_sign.resign(app) is False
print("test_bundle_sign OK")
