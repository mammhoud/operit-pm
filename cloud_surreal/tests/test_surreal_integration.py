"""Real embedded-SurrealKV integration.

Runs the datastore work in a subprocess: the SDK's embedded connection can abort
at interpreter shutdown (Rust panic after data is committed), so isolating it
keeps pytest's exit clean while still proving the file persists across opens.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(
    """
    import asyncio, json, sys
    from app.config import Settings
    from app.db.surreal import SurrealClient

    async def main():
        db_file = sys.argv[1]
        mode = sys.argv[2]
        settings = Settings(
            db_file=db_file, db_ns="planinc", db_name="planinc",
            jwt_secret="s", port=1111,
        )
        client = SurrealClient(settings)
        if mode == "write":
            await client.create("notes", {"content": "integration"})
            # Numeric record ids must round-trip: identity allocates
            # ``accounts:<n>`` and the frontend expects the numeric id.
            await client.create("accounts:1", {"name": "root", "role": "superadmin"})
        rows = await client.query("SELECT content FROM notes;")
        accounts = await client.query("SELECT name, role FROM accounts:1;")
        print(json.dumps({"notes": rows, "accounts": accounts}))

    asyncio.run(main())
    """
)


def _run(db_file: Path, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(db_file), mode],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def test_embedded_surrealkv_persists_across_opens(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(PROJECT_ROOT))
    db_file = tmp_path / "planinc.db"

    write = _run(db_file, "write")
    assert "integration" in write.stdout, write.stderr
    assert "root" in write.stdout, write.stderr
    assert db_file.exists()

    read = _run(db_file, "read")
    assert "integration" in read.stdout, read.stderr
    assert "superadmin" in read.stdout, read.stderr


_COLLAB_SCRIPT = textwrap.dedent(
    """
    import asyncio, json, sys
    from app.config import Settings
    from app.db.surreal import SurrealClient
    from app.domain.collab import (
        AgentDirectoryService,
        ConversationService,
        MessageService,
        ShareApprovalService,
    )

    async def main():
        db_file = sys.argv[1]
        settings = Settings(
            db_file=db_file, db_ns="planinc", db_name="planinc",
            jwt_secret="s", port=1111,
        )
        client = SurrealClient(settings)
        dirs = AgentDirectoryService(client)
        convos = ConversationService(client)
        messages = MessageService(client)
        approvals = ShareApprovalService(client)

        out = {}
        created = await dirs.create_dir(7, {"kind": "working", "path": "/repos/a"})
        out["dir_default"] = created["isDefault"]
        out["dirs"] = len(await dirs.list_dirs(7))

        convo = await convos.create_conversation(7, "chat")
        await messages.create_message(
            7, {"conversationId": convo["id"], "content": "hi", "role": "user"}
        )
        out["messages"] = len(await messages.list_messages(7, convo["id"]))
        try:
            await messages.list_messages(8, convo["id"])
            out["cross_account"] = "leaked"
        except Exception as err:  # noqa: BLE001 - report the code, not the type
            out["cross_account"] = getattr(err, "code", type(err).__name__)

        await client.create("notes:1", {"accountId": 7, "content": "plan"})
        await client.create("accounts:8", {"name": "friend"})
        [approval] = await approvals.request_internal(
            7, {"noteId": 1, "accountIds": [8]}
        )
        out["pending"] = approval["status"]
        out["access_before"] = await approvals.can_access_note(1, 8)
        await approvals.decide(8, False, {"id": approval["id"], "approve": True})
        out["approved"] = (await approvals.inbox(8, "approved"))[0]["status"]
        out["access_after"] = await approvals.can_access_note(1, 8)
        print(json.dumps(out))

    asyncio.run(main())
    """
)


def test_collab_slice_round_trips_on_the_embedded_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(PROJECT_ROOT))
    db_file = tmp_path / "collab.db"

    result = subprocess.run(
        [sys.executable, "-c", _COLLAB_SCRIPT, str(db_file)],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "dir_default": True,
        "dirs": 1,
        "messages": 1,
        "cross_account": "NOT_FOUND",
        "pending": "pending",
        "access_before": False,
        "approved": "approved",
        "access_after": True,
    }
