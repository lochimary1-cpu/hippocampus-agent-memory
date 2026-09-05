"""Filesystem access for the portable memory format."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def default_memory_dir() -> Path:
    configured = os.environ.get("HIPPOCAMPUS_MEMORY_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude" / "memory"


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}: {exc}") from exc


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".hippocampus-", dir=path.parent, text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def save_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def memory_content_dir(memory_dir: Path) -> Path:
    global_dir = memory_dir / "global"
    return global_dir if global_dir.is_dir() else memory_dir


def system_dir(memory_dir: Path) -> Path:
    return memory_dir / "_system"


def resolve_project_memory(cwd: str, ignored: Iterable[Path] = ()) -> Path | None:
    start = Path(cwd or Path.cwd()).expanduser()
    if start.is_file():
        start = start.parent
    ignored_paths = {path.expanduser().resolve() for path in ignored}
    for candidate in (start, *start.parents):
        memory_dir = candidate / ".claude" / "memory"
        if memory_dir.is_dir() and memory_dir.resolve() not in ignored_paths:
            return memory_dir
    return None
