"""Read a live SurrealDB (SurrealKV-backed) into a ``planinc-export-v1`` tree.

SurrealKV is an embedded store with no supported direct-file reader, so the
"native" read path is the database's own SQL endpoint: one
``SELECT * FROM <table>;`` per mapped table, staged into the exact JSONL layout
``planinc_export_surreal`` writes. Validation, checkpointing and
``import_surreal_workspace`` then run unchanged, so there is one mapping
contract instead of a second, divergent one.

Explicit target-model mapping (kept here so it is auditable in one place):

======================  ===================================================
Surreal table           Django target
======================  ===================================================
``accounts``            ``accounts.User``, plus ``tenancy.Membership`` and
                        ``workspaces.WorkspaceMember`` for the tenant
``notes``               ``notes.Note`` (``external_id`` =
                        ``surreal:notes:<id>``)
======================  ===================================================

A table outside this map is still staged (an operator can inspect what the
database holds) but the importer reports it as unmapped instead of silently
dropping it.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

FORMAT = "planinc-export-v1"
SURREAL_TABLE_MODEL_MAP: dict[str, str] = {
    "accounts": "accounts.User (+ tenancy.Membership, workspaces.WorkspaceMember)",
    "notes": "notes.Note (external_id=surreal:notes:<id>)",
}
DEFAULT_TABLES = ("accounts", "notes")
DEFAULT_TIMEOUT = 30.0


class SurrealReadError(RuntimeError):
    """The Surreal SQL endpoint refused the read or answered unexpectedly."""


def _auth_header(username: str | None, password: str | None) -> str | None:
    if not username:
        return None
    token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
    return f"Basic {token}"


def fetch_table(
    url: str,
    table: str,
    *,
    namespace: str,
    database: str,
    username: str | None = None,
    password: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """One table's rows, read through the Surreal SQL endpoint."""
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/sql"):
        endpoint = f"{endpoint}/sql"
    headers = {
        "Accept": "application/json",
        "Content-Type": "text/plain",
        "NS": namespace,
        "DB": database,
    }
    auth = _auth_header(username, password)
    if auth:
        headers["Authorization"] = auth

    request = Request(
        endpoint,
        data=f"SELECT * FROM {table};".encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError) as exc:
        raise SurrealReadError(f"Surreal read failed for {table!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SurrealReadError(f"Surreal answered non-JSON for {table!r}") from exc

    if not isinstance(payload, list) or not payload:
        raise SurrealReadError(f"Surreal returned no statement result for {table!r}")
    statement = payload[0] if isinstance(payload[0], dict) else {}
    status = str(statement.get("status", "")).upper()
    if status != "OK":
        detail = statement.get("detail") or statement.get("result")
        raise SurrealReadError(
            f"Surreal statement for {table!r} returned "
            f"{status or 'no status'}: {detail}"
        )
    rows = statement.get("result")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise SurrealReadError(f"Surreal result for {table!r} is not a row list")
    return [row for row in rows if isinstance(row, dict)]


def stage_export(
    destination: str | Path,
    tables: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    source: str = "surreal",
    uploads_index: Iterable[Mapping[str, Any]] = (),
) -> Path:
    """Write ``tables`` as a ``planinc-export-v1`` tree (manifest + checksums)."""
    root = Path(destination)
    records_dir = root / "records"
    checksums_dir = root / "checksums"
    uploads_dir = root / "uploads"
    for path in (records_dir, checksums_dir, uploads_dir):
        path.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "format": FORMAT,
        "source": source,
        "records": {},
        "sensitive_fields_omitted": True,
        "mapping": dict(SURREAL_TABLE_MODEL_MAP),
    }
    checksums: list[str] = []

    for table, rows in tables.items():
        path = records_dir / f"{table}.jsonl"
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
                    + "\n"
                )
                count += 1
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {path.relative_to(root).as_posix()}")
        manifest["records"][table] = count

    entries = list(uploads_index)
    index_path = uploads_dir / "index.jsonl"
    with index_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(
                json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
            )
    digest = hashlib.sha256(index_path.read_bytes()).hexdigest()
    checksums.append(f"{digest}  {index_path.relative_to(root).as_posix()}")
    manifest["files"] = {
        "count": len(entries),
        "bytes": sum(int(entry.get("size", 0) or 0) for entry in entries),
    }

    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (checksums_dir / "sha256sums").write_text(
        "\n".join(checksums) + ("\n" if checksums else ""), encoding="utf-8"
    )
    return root


def stage_from_surreal(
    destination: str | Path,
    *,
    url: str,
    namespace: str,
    database: str,
    tables: Iterable[str] = DEFAULT_TABLES,
    username: str | None = None,
    password: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Path:
    """Read every requested table and stage it as an export tree."""
    collected = {
        table: fetch_table(
            url,
            table,
            namespace=namespace,
            database=database,
            username=username,
            password=password,
            timeout=timeout,
        )
        for table in tables
    }
    return stage_export(destination, collected, source=url)
