"""Download the logbook from a Google Drive folder using a service account.

A DiveMate DB can't be reached with the narrow ``drive.file`` OAuth scope (the
phone app, not us, created it), and Drive has no "single folder" OAuth scope.
A **service account** solves both goals cleanly: you share *only* the target
folder with the service account's email, so its access is limited to that folder,
and it authenticates non-interactively with a key file — no repeated prompts.

Only two Drive REST calls are needed (list + download), so this uses just
``google-auth`` to mint a token and the stdlib for HTTP — no heavy client lib.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_API = "https://www.googleapis.com/drive/v3/files"


@dataclass
class GDriveConfig:
    key_file: str          # path to the service-account JSON key
    folder_id: str         # Drive folder ID the key can see (shared with the SA)
    file_name: str = "DiveMate.ddb"

    @classmethod
    def from_env(cls, key_file: str | None = None, folder_id: str | None = None,
                 file_name: str | None = None) -> "GDriveConfig":
        key = key_file or os.environ.get("DIVEMATE_GDRIVE_KEY")
        folder = folder_id or os.environ.get("DIVEMATE_GDRIVE_FOLDER")
        name = file_name or os.environ.get("DIVEMATE_GDRIVE_FILE") or "DiveMate.ddb"
        missing = [n for n, v in (("DIVEMATE_GDRIVE_KEY", key),
                                  ("DIVEMATE_GDRIVE_FOLDER", folder)) if not v]
        if missing:
            raise ValueError(
                "missing config: " + ", ".join(missing)
                + " (set the env var or pass the flag)"
            )
        return cls(key_file=key, folder_id=folder, file_name=name)


def _token(key_file: str) -> str:
    """Mint an OAuth access token from the service-account key."""
    try:
        from google.oauth2 import service_account  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "google-auth is required for 'pull'. Install with: "
            "pip install 'divemate-cli[gdrive]'"
        ) from e
    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=[SCOPE])
    creds.refresh(Request())
    return creds.token


def _get(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Google URL)
        return resp.read()


def find_file(cfg: GDriveConfig, token: str) -> dict[str, Any] | None:
    """Return metadata of the newest matching file in the folder, or None."""
    q = (f"'{cfg.folder_id}' in parents and name = '{cfg.file_name}' "
         f"and trashed = false")
    params = urllib.parse.urlencode({
        "q": q,
        "fields": "files(id,name,modifiedTime,size)",
        "orderBy": "modifiedTime desc",
        "pageSize": "1",
    })
    data = json.loads(_get(f"{_API}?{params}", token))
    files = data.get("files", [])
    return files[0] if files else None


def download(cfg: GDriveConfig, out_path: str | Path) -> dict[str, Any]:
    """Download the logbook to ``out_path`` atomically. Returns file metadata."""
    token = _token(cfg.key_file)
    meta = find_file(cfg, token)
    if meta is None:
        raise FileNotFoundError(
            f"'{cfg.file_name}' not found in folder {cfg.folder_id}. "
            f"Is the folder shared with the service account?"
        )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = _get(f"{_API}/{meta['id']}?alt=media", token)
    # atomic write: temp in the same dir, then replace
    fd, tmp = tempfile.mkstemp(dir=out_path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        os.replace(tmp, out_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    meta["bytes"] = len(content)
    meta["path"] = str(out_path)
    return meta
