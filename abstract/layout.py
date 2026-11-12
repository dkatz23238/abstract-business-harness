"""On-disk layout of a profile's data dir, and the move from the pre-0.2 one.

Before 0.2 (layout 1) every thread belonged to nobody:

    <data-root>/<profile>/state/{threads,history,memory,plans.db}
    <data-root>/<profile>/workspaces/<thread>/
    <data-root>/<profile>/workspace/            (CLI)

From 0.2 (layout 2) each user has a folder, and the old data becomes the
admin's:

    <data-root>/<profile>/users/<user>/state/...
    <data-root>/<profile>/users/<user>/workspaces/<thread>/
    <data-root>/<profile>/shared/memory/        ([memory] scope = "shared")
    <data-root>/<profile>/layout_version

`secrets.env` stays where it is. The server calls `migrate` only after the
user store opened, so a start that fails for a missing admin password never
moves anything. `abstract chat` and `history` call it too, so they never
write the new layout next to unmigrated data.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import ADMIN_USERNAME

LAYOUT_VERSION = 2
LAYOUT_FILENAME = "layout_version"
_LEGACY_ENTRIES = ("state", "workspaces", "workspace")


def migrate(profile) -> list[str]:
    """Bring `profile.data_dir` to the current layout. Returns what it did.

    Idempotent: a data dir already at layout 2 is left alone.
    """
    data_dir: Path = profile.data_dir
    marker = data_dir / LAYOUT_FILENAME
    notes: list[str] = []
    data_dir.mkdir(parents=True, exist_ok=True)
    if marker.is_file():
        return notes

    admin = profile.user_paths(ADMIN_USERNAME)
    for name in _LEGACY_ENTRIES:
        src = data_dir / name
        if not src.exists():
            continue
        dest = admin.root / name
        if dest.exists():
            notes.append(f"left {src} in place: {dest} already exists")
            continue
        admin.root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        notes.append(f"moved {src} -> {dest}")
    old_memory = admin.state_dir / "memory"
    target = profile.shared_memory_dir
    if profile.memory_scope == "shared" and old_memory.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_memory), str(target))
        notes.append(f"moved {old_memory} -> {target} (memory scope shared)")
    profile.users_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{LAYOUT_VERSION}\n")
    if notes:
        notes.append(f"wrote {marker}")
    return notes
