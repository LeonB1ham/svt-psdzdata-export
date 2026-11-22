"""Upload to MEGA through the official MEGAcmd tools (https://mega.io/cmd).

No password is stored here: log in once with MEGAcmd (`mega-login email password`
or the MEGAcmd window). The upload uses `mega-put` and gets a public link
with `mega-export`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MEGACMD_URL = "https://mega.io/cmd"
DEFAULT_REMOTE_DIR = "/SVT-Export"
LINK_RE = re.compile(r"https://mega\.(?:nz|io)/\S+")


class MegaError(Exception):
    """code: 'not_installed', 'not_logged_in' or 'failed'."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform == "win32":
        for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                dirs.append(Path(base) / "MEGAcmd")
    elif sys.platform == "darwin":
        dirs.append(Path("/Applications/MEGAcmd.app/Contents/MacOS"))
    else:
        dirs.append(Path("/usr/bin"))
    return dirs


def find_tool(name: str) -> str | None:
    """Path of a MEGAcmd command such as 'mega-put'."""
    found = shutil.which(name)
    if found:
        return found
    for folder in _candidate_dirs():
        for suffix in (".bat", ".cmd", ".exe", ""):
            path = folder / f"{name}{suffix}"
            if path.is_file():
                return str(path)
    return None


def is_installed() -> bool:
    return find_tool("mega-put") is not None


def _run(name: str, *args: str, timeout: int | None = 60) -> subprocess.CompletedProcess:
    tool = find_tool(name)
    if tool is None:
        raise MegaError("not_installed", MEGACMD_URL)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            [tool, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise MegaError("failed", f"{name}: timeout") from exc
    except OSError as exc:
        raise MegaError("failed", f"{name}: {exc}") from exc


def whoami() -> str:
    """E-mail of the logged-in MEGA account; raises MegaError if not logged in."""
    result = _run("mega-whoami")
    text = (result.stdout + result.stderr).strip()
    hit = re.search(r"e-?mail:\s*(\S+)", text, re.IGNORECASE)
    if result.returncode == 0 and hit:
        return hit.group(1)
    raise MegaError("not_logged_in", text)


def upload(path: str | Path, remote_dir: str = DEFAULT_REMOTE_DIR, progress=None) -> str:
    """Upload a file or folder and return its public link."""
    local = Path(path)
    if not local.exists():
        raise MegaError("failed", f"{local} not found")
    whoami()
    remote_dir = "/" + remote_dir.strip("/") if remote_dir.strip("/") else "/"
    if progress:
        progress("upload", local.name)
    result = _run("mega-put", "-c", str(local), remote_dir + "/", timeout=None)
    if result.returncode != 0:
        raise MegaError("failed", (result.stderr or result.stdout).strip())
    remote = f"{remote_dir.rstrip('/')}/{local.name}"
    if progress:
        progress("link", remote)
    result = _run("mega-export", "-a", "-f", remote)
    text = result.stdout + result.stderr
    hit = LINK_RE.search(text)
    if not hit:
        # Already exported earlier: list the existing link.
        result = _run("mega-export", remote)
        hit = LINK_RE.search(result.stdout + result.stderr)
    if not hit:
        raise MegaError("failed", text.strip())
    return hit.group(0).rstrip(").,;")
