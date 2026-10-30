"""Self-update from GitHub releases.

The packaged app checks the repository's releases, downloads the build for
this OS, and swaps it in for the running copy:

* Windows: the running exe can't be overwritten, but it can be renamed. So
  ``Telecaster.exe`` becomes ``Telecaster.exe.old``, the download takes its
  place, and the new copy deletes the old one on its next start.
* macOS: the zip is unpacked next to the app and the two bundles swap names.

Installs from source (pip) are only told that a new version exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import __version__

REPO = "FrankTreadwell/telecaster"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
PIP_COMMAND = f"pip install -U git+https://github.com/{REPO}"
MAC_STAGING = ".Telecaster-update"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?$")
_STAGES = {"a": 0, "b": 1, "rc": 2, None: 3}  # a final release sorts after its betas


def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """A sortable key for versions like ``0.2.0``, ``v0.2.0b2`` or ``1.0rc1``."""
    match = _VERSION.match(text.strip())
    if not match:
        return None
    major, minor, patch, stage, number = match.groups()
    return int(major), int(minor), int(patch or 0), _STAGES[stage], int(number or 0)


def is_prerelease(text: str) -> bool:
    key = parse_version(text)
    return key is not None and key[3] != _STAGES[None]


@dataclass(frozen=True)
class Release:
    version: str
    page_url: str
    asset_name: str = ""
    asset_url: str = ""
    size: int = 0
    sha256: str = ""  # hex; empty if GitHub didn't publish a digest


def asset_suffix(platform: str = sys.platform) -> str | None:
    """End of the release asset name built for this OS (see the release workflow)."""
    if platform == "win32":
        return "-windows-x64.exe"
    if platform == "darwin":
        return "-macos-arm64.zip"
    return None


def pick_update(releases: list, current: str = __version__, suffix: str | None = None) -> Release | None:
    """The newest release above ``current``, from GitHub's ``/releases`` JSON.

    Pre-releases are only offered to someone already running one. With a
    ``suffix``, only releases that ship a matching download count.
    """
    current_key = parse_version(current)
    if current_key is None:
        return None
    allow_pre = is_prerelease(current)
    best: tuple[tuple, Release] | None = None
    for item in releases if isinstance(releases, list) else []:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        if item.get("prerelease") and not allow_pre:
            continue
        version = str(item.get("tag_name", "")).lstrip("v")
        key = parse_version(version)
        if key is None or key <= current_key or (best and key <= best[0]):
            continue
        release = Release(version, str(item.get("html_url") or RELEASES_PAGE))
        if suffix:
            asset = next((a for a in item.get("assets") or []
                          if isinstance(a, dict) and str(a.get("name", "")).endswith(suffix)), None)
            if asset is None:
                continue
            digest = str(asset.get("digest") or "")
            release = Release(
                version, release.page_url, str(asset["name"]), str(asset.get("browser_download_url", "")),
                int(asset.get("size") or 0), digest[7:].lower() if digest.startswith("sha256:") else "")
            if not release.asset_url:
                continue
        best = (key, release)
    return best[1] if best else None


# -- the installed copy ------------------------------------------------------

def installed_path() -> Path | None:
    """The exe (Windows) or .app bundle (macOS) to replace; None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "win32":
        return exe
    if sys.platform == "darwin" and len(exe.parents) > 2 and exe.parents[2].suffix == ".app":
        return exe.parents[2]  # Telecaster.app/Contents/MacOS/Telecaster
    return None


def download_path(target: Path) -> Path:
    """Where to save the download: beside the target, so the swap is a same-disk rename."""
    return target.with_name(target.name + ".download")


def can_replace(target: Path) -> bool:
    probe = download_path(target)
    try:
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup(target: Path | None = None) -> None:
    """Remove what a previous update left behind. Safe to call on every start."""
    target = target or installed_path()
    if target is None:
        return
    leftovers = [target.with_name(target.name + ".old"), download_path(target)]
    for path in leftovers:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # the old copy may still be exiting; next start gets it
    shutil.rmtree(target.parent / MAC_STAGING, ignore_errors=True)


def install(target: Path, download: Path) -> None:
    """Swap the downloaded build in for ``target``. Raises OSError on failure, leaving ``target`` intact."""
    if target.suffix == ".app":
        _install_app(target, download)
        return
    old = target.with_name(target.name + ".old")
    old.unlink(missing_ok=True)
    os.replace(target, old)
    try:
        os.replace(download, target)
    except OSError:
        os.replace(old, target)
        raise


def _install_app(target: Path, download: Path) -> None:
    staging = target.parent / MAC_STAGING
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    # ditto keeps the bundle's symlinks and code signature intact.
    result = subprocess.run(["ditto", "-x", "-k", str(download), str(staging)], capture_output=True)
    if result.returncode != 0:
        raise OSError(f"couldn't unpack the update: {result.stderr.decode(errors='replace').strip()}")
    new = next(staging.glob("*.app"), None)
    if new is None:
        raise OSError("the update doesn't contain an app")
    old = staging / "previous.app"
    os.replace(target, old)
    try:
        os.replace(new, target)
    except OSError:
        os.replace(old, target)
        raise
    download.unlink(missing_ok=True)


def relaunch(target: Path) -> None:
    """Start the (new) installed copy as a separate process."""
    if target.suffix == ".app":
        subprocess.Popen(["open", "-n", str(target)])
        return
    env = dict(os.environ)
    # Make the new one-file exe unpack itself instead of reusing this process's files.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(target)], env=env, creationflags=flags, close_fds=True)


# -- network -----------------------------------------------------------------

class Updater(QObject):
    """Checks GitHub for a newer release and downloads it."""

    available = Signal(object)        # Release
    up_to_date = Signal()
    check_failed = Signal(str)
    progress = Signal(int, int)       # bytes received, total
    downloaded = Signal(object)       # Path of the verified download
    download_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._net = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._file = None

    @property
    def busy(self) -> bool:
        return self._reply is not None

    def check(self) -> None:
        if self.busy:
            return
        request = QNetworkRequest(QUrl(RELEASES_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"User-Agent", f"Telecaster/{__version__}".encode())
        request.setTransferTimeout(10000)
        self._reply = reply = self._net.get(request)
        reply.finished.connect(lambda: self._on_checked(reply))

    def _on_checked(self, reply: QNetworkReply) -> None:
        self._reply = None
        reply.deleteLater()
        if reply.error() != QNetworkReply.NoError:
            self.check_failed.emit(reply.errorString())
            return
        try:
            releases = json.loads(bytes(reply.readAll()))
        except ValueError:
            self.check_failed.emit("GitHub sent an unexpected answer")
            return
        suffix = asset_suffix() if installed_path() else None
        release = pick_update(releases, suffix=suffix)
        if release is None:
            self.up_to_date.emit()
        else:
            self.available.emit(release)

    def download(self, release: Release, target: Path) -> None:
        if self.busy:
            return
        path = download_path(target)
        try:
            self._file = open(path, "wb")
        except OSError as exc:
            self.download_failed.emit(str(exc))
            return
        request = QNetworkRequest(QUrl(release.asset_url))
        request.setRawHeader(b"User-Agent", f"Telecaster/{__version__}".encode())
        request.setTransferTimeout(30000)
        self._reply = reply = self._net.get(request)  # GitHub redirects to its CDN; Qt follows https redirects
        reply.readyRead.connect(lambda: self._file.write(bytes(reply.readAll())))
        reply.downloadProgress.connect(lambda done, total: self.progress.emit(done, total))
        reply.finished.connect(lambda: self._on_downloaded(reply, release, path))

    def _on_downloaded(self, reply: QNetworkReply, release: Release, path: Path) -> None:
        self._reply = None
        reply.deleteLater()
        self._file.write(bytes(reply.readAll()))
        self._file.close()
        problem = None
        if reply.error() != QNetworkReply.NoError:
            problem = reply.errorString()
        elif release.size and path.stat().st_size != release.size:
            problem = "the download was incomplete"
        elif release.sha256 and file_sha256(path) != release.sha256:
            problem = "the download didn't match GitHub's checksum"
        if problem:
            path.unlink(missing_ok=True)
            self.download_failed.emit(problem)
        else:
            self.downloaded.emit(path)
