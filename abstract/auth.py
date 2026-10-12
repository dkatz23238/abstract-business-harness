"""API key for the HTTP API and the web UI sign-in.

One key, stored at `<data-root>/api_key` (mode 0600). The first startup
creates it: `HARNESS_UI_TOKEN` is the initial value when that env var is
set, otherwise a random `bh_…` key is generated. Later startups keep the
file, so rotating the key from the admin API survives a restart.
`HARNESS_UI_TOKEN` does not override a key that is already on disk.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

API_KEY_FILENAME = "api_key"


def generate_api_key() -> str:
    return "bh_" + secrets.token_urlsafe(32)


class ApiKey:
    """The live key. Rotation updates `value` in place so middleware sees it."""

    def __init__(self, value: str, path: Path):
        self.value = value
        self.path = path

    def rotate(self) -> str:
        new = generate_api_key()
        write_api_key(self.path, new)
        self.value = new
        return new


def load_or_create_api_key(data_root: Path, seed: str | None = None) -> ApiKey:
    path = Path(data_root).expanduser() / API_KEY_FILENAME
    if path.is_file():
        value = path.read_text().strip()
        if value:
            _harden(path)
            return ApiKey(value, path)
    value = seed.strip() if seed and seed.strip() else generate_api_key()
    write_api_key(path, value)
    return ApiKey(value, path)


def write_api_key(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, (value.strip() + "\n").encode())
    finally:
        os.close(fd)
    _harden(path)


def _harden(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return
