"""Engine configuration — everything that is NOT customer-specific.

The engine knows about serving, storage and auth; it knows nothing about any
particular business domain. Domain configuration (model, tools, skills,
instructions, credentials) lives in a profile directory and is loaded by
`harness.profile`.

Environment:
  HARNESS_DATA_ROOT     where per-profile state and workspaces live
                        (default: <repo>/data). One subdirectory per
                        profile id, so two profiles never share threads.
  HARNESS_ADMIN_PASSWORD  password of the `admin` user. Required on the first
                        start (no <data-root>/users.json yet), ignored after.
  HARNESS_ADMIN_API_KEY optional: the admin's API key on that first start.
                        Unset = a pre-0.2 <data-root>/api_key is kept, else
                        a key is generated and printed once.
  HARNESS_UI_ORIGINS    comma-separated CORS origins for the web UI.
  HARNESS_UI_DIR        built web UI (index.html). Default: <repo>/ui/dist
                        when that build exists; unset means API only.
  LOGFIRE_TOKEN         optional live tracing.
  OPENAI_BASE_URL       optional base URL for OpenAI-compatible `openai:`
                        specs. Unset uses https://api.openai.com/v1.
                        Example: https://openrouter.ai/api/v1
  ANTHROPIC_API_KEY     key for `anthropic:` specs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent

# The account created on first start. It owns the data from before 0.2 and
# can be neither deleted nor demoted.
ADMIN_USERNAME = "admin"

# Replaced in 0.2 by user accounts; the server warns when they are still set.
LEGACY_ENV_VARS = ("HARNESS_UI_TOKEN", "HARNESS_ADMIN_TOKEN")


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency): real env vars win over the file."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(REPO_DIR / ".env")


def ui_dir_from_env() -> Path | None:
    """Directory of a production UI build, or None when the API is alone.

    `HARNESS_UI_DIR` wins. Otherwise `<repo>/ui/dist` is used when it
    already contains `index.html` (a local `npm run build`).
    """
    raw = os.environ.get("HARNESS_UI_DIR")
    if raw is not None:
        text = raw.strip()
        return Path(text).expanduser() if text else None
    candidate = REPO_DIR / "ui" / "dist"
    if (candidate / "index.html").is_file():
        return candidate
    return None


@dataclass
class EngineSettings:
    data_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HARNESS_DATA_ROOT", REPO_DIR / "data")
        ).expanduser()
    )
    admin_password: str | None = field(
        default_factory=lambda: os.environ.get("HARNESS_ADMIN_PASSWORD")
    )
    admin_api_key: str | None = field(
        default_factory=lambda: os.environ.get("HARNESS_ADMIN_API_KEY")
    )
    ui_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.environ.get(
                "HARNESS_UI_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:5174,http://127.0.0.1:5174,"
                "http://localhost:5175,http://127.0.0.1:5175",
            ).split(",")
            if o.strip()
        ]
    )
    ui_dir: Path | None = field(default_factory=ui_dir_from_env)

    def profile_data_dir(self, profile_id: str) -> Path:
        return self.data_root / profile_id
