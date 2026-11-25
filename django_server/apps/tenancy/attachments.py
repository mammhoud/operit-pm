"""Copy indexed export uploads into the tenant-prefixed media tree.

The export records every upload as ``{path, size, sha256}``. Importing the
bytes is the second half of attachment migration: each file is copied under
``<media-root>/<tenant>/<path>`` and re-hashed, so a truncated or tampered file
is reported instead of becoming a silently broken note attachment.

``path`` is treated as hostile input. Absolute paths, ``..`` segments and
entries that resolve outside the source root (symlinks included) are rejected
as ``unsafe_path`` and never read.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def _safe_relative(raw: str) -> Path | None:
    candidate = Path(str(raw))
    if candidate.is_absolute() or not candidate.parts:
        return None
    if any(part in ("", ".", "..") for part in candidate.parts):
        return None
    return candidate


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def copy_indexed_uploads(
    index_path: str | Path,
    source_root: str | Path,
    destination_root: str | Path,
    tenant: str,
) -> dict:
    """Copy every indexed upload into ``destination_root/<tenant>/<path>``."""
    index_path = Path(index_path)
    source_root = Path(source_root)
    tenant_root = Path(destination_root) / str(tenant)
    report: dict = {
        "copied": 0,
        "bytes": 0,
        "missing": [],
        "mismatched": [],
        "unsafe": [],
    }
    if not index_path.is_file():
        report["missing"].append(index_path.as_posix())
        return report

    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            report["mismatched"].append(
                {"path": line[:120], "reason": "bad index line"}
            )
            continue

        raw_path = str(entry.get("path", ""))
        relative = _safe_relative(raw_path)
        if relative is None:
            report["unsafe"].append(raw_path)
            continue

        source = source_root / relative
        if not source.is_file() or not _inside(source_root, source):
            report["unsafe"].append(raw_path)
            continue

        data = source.read_bytes()
        expected_size = int(entry.get("size", -1) or -1)
        expected_digest = str(entry.get("sha256", ""))
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != expected_size or digest != expected_digest:
            report["mismatched"].append(raw_path)
            continue

        target = tenant_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        report["copied"] += 1
        report["bytes"] += len(data)

    return report
