"""Engine configuration — everything that is NOT customer-specific.

The engine knows about serving, storage and auth; it knows nothing about any
particular business domain. Domain configuration (model, tools, skills,
instructions, credentials) lives in a profile directory and is loaded by
`harness.profile`.

Environment:
  HARNESS_DATA_ROOT     where per-profile state and workspaces live
                        (default: <repo>/data). One subdirectory per
                        profile id, so two profiles never share threads.
  HARNESS_UI_TOKEN      when set, every HTTP request must present it
                        (Bearer header or ?token=).
  HARNESS_ADMIN_TOKEN   required by the /admin endpoints that rewrite a
                        profile. Unset = admin API disabled entirely.
  HARNESS_UI_ORIGINS    comma-separated CORS origins for the web UI.
  LOGFIRE_TOKEN         optional live tracing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent


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


@dataclass
class EngineSettings:
    data_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HARNESS_DATA_ROOT", REPO_DIR / "data")
        ).expanduser()
    )
    ui_token: str | None = field(default_factory=lambda: os.environ.get("HARNESS_UI_TOKEN"))
    admin_token: str | None = field(default_factory=lambda: os.environ.get("HARNESS_ADMIN_TOKEN"))
    ui_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in os.environ.get(
                "HARNESS_UI_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if o.strip()
        ]
    )

    def profile_data_dir(self, profile_id: str) -> Path:
        return self.data_root / profile_id
