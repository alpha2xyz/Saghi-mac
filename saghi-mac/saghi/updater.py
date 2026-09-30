"""
In-app updates from this repo's GitHub releases, so the app can be updated
with one click, without a terminal.

Two kinds of update (the full app is ~4 GB, almost all of it the model,
which does not change between releases):

  light  The release ships `Saghi-mac-app-v<version>.zip` -- just the
         `saghi/` package (with its `_version.py`) and the pinned
         requirements file. When the new release's requirements hash
         (REQUIREMENTS_SHA256 in its `_version.py`) equals the installed
         one, the Python libraries are unchanged, so swapping the `saghi/`
         folder is the whole update: a few MB, a few seconds.
  full   The libraries changed. Download the release's
         `Install-Saghi-mac.command` -- the same installer a first install
         uses -- and run it in Terminal once Saghi has quit.

Every downloaded file is checked against the release's SHA256SUMS before it
is used; a file with no checksum is never used.

The flow, as the GUI drives it (ui/update_dialog.py, on a background
thread -- nothing here touches Qt):

    result = check()                       # small GitHub API call
    prepared = prepare(result.latest, ...) # download + verify (+ extract)
    apply_light(prepared) / launch_full_installer(prepared)
    relaunch_after_exit() / (quit)

Only the installed app (Saghi.app/Contents/Resources/...) can update
itself; a source checkout just reports what is available.
"""

from __future__ import annotations

import hashlib
import logging
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import REQUIREMENTS_SHA256, __version__, paths

logger = logging.getLogger("saghi.updater")

REPO = "alpha2xyz/Saghi-mac"
API_RELEASES = f"https://api.github.com/repos/{REPO}/releases"
INSTALLER_ASSET = "Install-Saghi-mac.command"
SUMS_ASSET = "SHA256SUMS"
_TIMEOUT_S = 20.0
_USER_AGENT = "Saghi-mac-updater"

ProgressFn = Callable[[int, int], None]  # bytes_done, bytes_total (0 = unknown)


class UpdateError(Exception):
    """A user-presentable failure (network, checksum, missing file, ...)."""


# ---- versions ---------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


def parse_version(text: str) -> tuple:
    """'v0.2.10' -> (0, 2, 10). Anything unparseable -> (0,)."""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return (0,)
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()  # 0.2.0 == 0.2
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def light_asset_name(version: str) -> str:
    return f"Saghi-mac-app-v{version}.zip"


# ---- release lookup ------------------------------------------------------------


@dataclass
class ReleaseInfo:
    tag: str
    version: str
    notes: str
    html_url: str
    assets: Dict[str, str] = field(default_factory=dict)  # asset name -> download URL


@dataclass
class CheckResult:
    current: str
    latest: Optional[ReleaseInfo]
    update_available: bool


def pick_latest(releases: List[dict]) -> Optional[ReleaseInfo]:
    """
    The newest app release: tag like v<number> (the model-v1 release is
    skipped, same rule as the installer), not a draft or pre-release.
    Picks the highest version rather than trusting the API's order.
    """
    best: Optional[ReleaseInfo] = None
    for rel in releases:
        tag = rel.get("tag_name") or ""
        if rel.get("draft") or rel.get("prerelease") or not re.match(r"^v\d", tag):
            continue
        info = ReleaseInfo(
            tag=tag,
            version=tag[1:],
            notes=(rel.get("body") or "").strip(),
            html_url=rel.get("html_url") or "",
            assets={a.get("name", ""): a.get("browser_download_url", "") for a in rel.get("assets") or []},
        )
        if best is None or is_newer(info.version, best.version):
            best = info
    return best


def _client():
    import httpx

    return httpx.Client(
        timeout=_TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )


def fetch_releases() -> List[dict]:
    try:
        with _client() as client:
            resp = client.get(API_RELEASES, params={"per_page": 20})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 -- any network/HTTP/JSON failure is the same thing to the user
        raise UpdateError(f"could not reach GitHub ({exc})") from exc
    if not isinstance(data, list):
        raise UpdateError("unexpected reply from GitHub")
    return data


def check(current: str = __version__) -> CheckResult:
    latest = pick_latest(fetch_releases())
    return CheckResult(
        current=current,
        latest=latest,
        update_available=bool(latest and is_newer(latest.version, current)),
    )


# ---- download + verify ----------------------------------------------------------


def parse_sums(text: str) -> Dict[str, str]:
    """SHA256SUMS lines ('<hex>  <name>' or '<hex> *<name>') -> {name: hex}."""
    sums: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*").strip()] = parts[0].lower()
    return sums


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path, name: str, sums: Dict[str, str]) -> None:
    expected = sums.get(name)
    if not expected:
        raise UpdateError(f"no checksum for {name}")
    if sha256_file(path) != expected:
        raise UpdateError(f"checksum mismatch for {name} (damaged download)")


def download(url: str, dest: Path, on_progress: Optional[ProgressFn] = None) -> Path:
    tmp = dest.parent / (dest.name + ".part")
    try:
        with _client() as client, client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
    except UpdateError:
        raise
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"download failed ({exc})") from exc
    os.replace(tmp, dest)
    return dest


# ---- prepare --------------------------------------------------------------------------


@dataclass
class PreparedUpdate:
    kind: str  # "light" | "full"
    version: str
    workdir: Path
    package_dir: Optional[Path] = None  # light: the verified, extracted new saghi/
    installer: Optional[Path] = None  # full: the verified installer script


_REQ_SHA_RE = re.compile(r'^REQUIREMENTS_SHA256\s*=\s*["\']([0-9a-fA-F]*)["\']', re.MULTILINE)


def _requirements_sha_of(package_dir: Path) -> str:
    """Read REQUIREMENTS_SHA256 from an extracted saghi/_version.py as text (never imported)."""
    try:
        m = _REQ_SHA_RE.search((package_dir / "_version.py").read_text(encoding="utf-8"))
    except OSError:
        return ""
    return m.group(1).lower() if m else ""


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if dest_resolved not in target.parents and target != dest_resolved:
                raise UpdateError(f"unsafe path in update archive: {member.filename}")
        zf.extractall(dest)


def updates_dir() -> Path:
    d = paths.data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare(
    release: ReleaseInfo,
    installed_requirements_sha: str = REQUIREMENTS_SHA256,
    on_progress: Optional[ProgressFn] = None,
    workdir: Optional[Path] = None,
) -> PreparedUpdate:
    """
    Download what `release` needs and verify it. Tries the light update
    first; falls back to the full installer when the release has no light
    archive or its libraries differ from the installed ones.
    """
    sums_url = release.assets.get(SUMS_ASSET)
    if not sums_url:
        raise UpdateError(f"release {release.tag} has no {SUMS_ASSET}")
    workdir = workdir or Path(tempfile.mkdtemp(prefix=f"saghi-{release.version}-", dir=updates_dir()))
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        return _prepare_in(release, installed_requirements_sha, on_progress, workdir, sums_url)
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _prepare_in(
    release: ReleaseInfo,
    installed_requirements_sha: str,
    on_progress: Optional[ProgressFn],
    workdir: Path,
    sums_url: str,
) -> PreparedUpdate:
    sums = parse_sums(download(sums_url, workdir / SUMS_ASSET).read_text(encoding="utf-8", errors="replace"))

    light_name = light_asset_name(release.version)
    light_url = release.assets.get(light_name)
    if light_url and installed_requirements_sha:
        zip_path = download(light_url, workdir / light_name, on_progress)
        verify_file(zip_path, light_name, sums)
        extract_dir = workdir / "extracted"
        _safe_extract(zip_path, extract_dir)
        package_dir = extract_dir / "saghi"
        if not (package_dir / "__init__.py").exists():
            raise UpdateError(f"{light_name} does not contain saghi/")
        if _requirements_sha_of(package_dir) == installed_requirements_sha.lower():
            return PreparedUpdate(kind="light", version=release.version, workdir=workdir, package_dir=package_dir)
        logger.info("Release %s changes the Python libraries -- a full update is needed", release.tag)

    installer_url = release.assets.get(INSTALLER_ASSET)
    if not installer_url:
        raise UpdateError(f"release {release.tag} has no {INSTALLER_ASSET}")
    installer = download(installer_url, workdir / INSTALLER_ASSET, on_progress)
    verify_file(installer, INSTALLER_ASSET, sums)
    installer.chmod(0o755)
    return PreparedUpdate(kind="full", version=release.version, workdir=workdir, installer=installer)


# ---- apply ------------------------------------------------------------------------------


def installed_package_dir() -> Path:
    return Path(__file__).resolve().parent


def can_self_update() -> bool:
    """Only the installed app bundle can replace its own code."""
    return paths.app_resources_dir() is not None


def apply_light(prepared: PreparedUpdate, target: Optional[Path] = None) -> Path:
    """
    Swap the running app's `saghi/` folder for the new one. The old folder
    is kept next to it as `saghi.previous` (a manual rollback is one rename
    away), and if the swap itself fails the old folder is put back.
    Returns the backup path.

    Python has already imported every module the running process needs, so
    replacing the files underneath it is safe; the new code runs after the
    relaunch.
    """
    if prepared.kind != "light" or prepared.package_dir is None:
        raise UpdateError("not a light update")
    target = target or installed_package_dir()
    staging = target.parent / f"{target.name}.incoming"
    backup = target.parent / f"{target.name}.previous"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(prepared.package_dir, staging)
    shutil.rmtree(backup, ignore_errors=True)
    os.replace(target, backup)
    try:
        os.replace(staging, target)
    except OSError as exc:
        os.replace(backup, target)
        raise UpdateError(f"could not install the new files ({exc})") from exc
    _set_bundle_version(prepared.version)
    shutil.rmtree(prepared.workdir, ignore_errors=True)
    logger.info("Light update to %s installed (backup: %s)", prepared.version, backup)
    return backup


def _set_bundle_version(version: str) -> None:
    """Best effort: keep Saghi.app's Info.plist version in step (Finder's Get Info shows it)."""
    resources = paths.app_resources_dir()
    if resources is None:
        return
    info = resources.parent / "Info.plist"
    try:
        with open(info, "rb") as f:
            data = plistlib.load(f)
        data["CFBundleVersion"] = version
        data["CFBundleShortVersionString"] = version
        with open(info, "wb") as f:
            plistlib.dump(data, f)
    except Exception:  # noqa: BLE001
        logger.debug("Could not update Info.plist version", exc_info=True)


def _spawn_after_exit(command: List[str]) -> None:
    """
    Run `command` once this process has exited (so the installer's "is
    Saghi running?" check passes, and a relaunch does not start a second
    copy). Waits at most ~60 s.
    """
    pid = os.getpid()
    quoted = " ".join(_sh_quote(c) for c in command)
    script = f"i=0; while kill -0 {pid} 2>/dev/null && [ $i -lt 120 ]; do sleep 0.5; i=$((i+1)); done; {quoted}"
    subprocess.Popen(
        ["/bin/sh", "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def relaunch_after_exit() -> None:
    """Reopen Saghi.app once this process quits (call right before quitting)."""
    resources = paths.app_resources_dir()
    if resources is None:
        return
    _spawn_after_exit(["/usr/bin/open", str(resources.parent.parent)])


def launch_full_installer_after_exit(prepared: PreparedUpdate) -> None:
    """Open the verified installer in Terminal once this process quits (call right before quitting)."""
    if prepared.kind != "full" or prepared.installer is None:
        raise UpdateError("not a full update")
    _spawn_after_exit(["/usr/bin/open", "-a", "Terminal", str(prepared.installer)])
