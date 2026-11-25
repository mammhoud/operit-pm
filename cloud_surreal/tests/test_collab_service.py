"""S6 collaboration rules: ownership gates, share layering, and policy."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.collab import (
    AgentDirectoryService,
    ConversationService,
    FollowsService,
    MessageService,
    ShareApprovalService,
    decode_conversation_share_id,
    derive_agent_dir_label,
    site_origin,
    validate_agent_path,
)
from app.domain.errors import DomainError
from tests.fakes import FakeDb

OWNER = 1
OTHER = 2


def _run(coro):
    return asyncio.run(coro)


def _code(coro) -> str:
    with pytest.raises(DomainError) as caught:
        _run(coro)
    return caught.value.code


# -- helpers ---------------------------------------------------------------


def _note(db: FakeDb, note_id: int = 1, account: int = OWNER) -> dict:
    db.seed(
        "notes",
        note_id,
        {
            "accountId": account,
            "content": "Quarterly plan\nbody",
            "isShare": False,
            "shareEncryptedUrl": None,
            "sharePassword": "",
        },
    )
    return {"id": f"notes:{note_id}", **db._table("notes")[note_id]}


def _conversation(db: FakeDb, conversation_id: int = 1, account: int = OWNER) -> None:
    db.seed(
        "conversation",
        conversation_id,
        {"accountId": account, "title": "chat", "isShare": False},
    )


# -- agent directories -----------------------------------------------------


def test_agent_dir_label_falls_back_to_path_basename():
    assert derive_agent_dir_label("", "/Users/me/work/") == "work"
    assert derive_agent_dir_label("  Notes  ", "/tmp/x") == "Notes"
    assert derive_agent_dir_label("", "C:\\repo\\src") == "src"


def test_agent_path_mirrors_the_ts_schema():
    assert validate_agent_path("  /repos/a  ") == "/repos/a"
    assert _code_text(lambda: validate_agent_path("")) == "BAD_REQUEST"
    assert _code_text(lambda: validate_agent_path("/a/../b")) == "BAD_REQUEST"
    assert _code_text(lambda: validate_agent_path("/a\0b")) == "BAD_REQUEST"
    assert _code_text(lambda: validate_agent_path("/" + "x" * 512)) == "BAD_REQUEST"


def test_agent_dir_create_makes_the_first_entry_default_and_dedupes_path():
    db = FakeDb()
    dirs = AgentDirectoryService(db)

    first = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/repos/a"}))
    assert first["isDefault"] is True
    assert first["label"] == "a"
    assert first["sortOrder"] == 0

    second = _run(
        dirs.create_dir(
            OWNER, {"kind": "working", "path": "/repos/b", "isDefault": False}
        )
    )
    assert second["isDefault"] is False
    assert second["sortOrder"] == 1

    assert (
        _code(dirs.create_dir(OWNER, {"kind": "working", "path": "/repos/b"}))
        == "CONFLICT"
    )


def test_agent_dir_only_one_default_survives_per_kind():
    db = FakeDb()
    dirs = AgentDirectoryService(db)
    first = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/a"}))
    second = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/b"}))

    updated = _run(
        dirs.update_dir(OWNER, {"id": second["id"], "isDefault": True, "label": "B"})
    )
    assert updated["isDefault"] is True
    stored = _run(dirs.list_dirs(OWNER))
    defaults = [row for row in stored if row["isDefault"]]
    assert [row["id"] for row in defaults] == [second["id"]]
    assert first["id"] != second["id"]


def test_agent_dir_update_is_account_scoped_and_rejects_duplicate_path():
    db = FakeDb()
    dirs = AgentDirectoryService(db)
    mine = _run(dirs.create_dir(OWNER, {"kind": "skills", "path": "/skills/a"}))
    _run(dirs.create_dir(OWNER, {"kind": "skills", "path": "/skills/b"}))
    _run(dirs.create_dir(OTHER, {"kind": "skills", "path": "/skills/c"}))

    assert (
        _code(dirs.update_dir(OWNER, {"id": mine["id"], "path": "/skills/b"}))
        == "CONFLICT"
    )
    # Another account's row is invisible, not writable.
    assert (
        _code(
            dirs.update_dir(
                OTHER, {"id": mine["id"], "label": "hijack", "path": "/skills/a"}
            )
        )
        == "NOT_FOUND"
    )


def test_agent_dir_reorder_ignores_foreign_and_unknown_ids():
    db = FakeDb()
    dirs = AgentDirectoryService(db)
    a = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/a"}))
    b = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/b"}))
    foreign = _run(dirs.create_dir(OTHER, {"kind": "working", "path": "/z"}))

    ordered = _run(
        dirs.reorder(OWNER, "working", [b["id"], foreign["id"], a["id"], 999])
    )
    assert [row["id"] for row in ordered] == [b["id"], a["id"]]
    assert [row["sortOrder"] for row in ordered] == [0, 1]


def test_agent_dir_delete_promotes_a_new_default_and_densifies_order():
    db = FakeDb()
    dirs = AgentDirectoryService(db)
    a = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/a"}))
    b = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/b"}))
    c = _run(dirs.create_dir(OWNER, {"kind": "working", "path": "/c"}))

    _run(dirs.delete_dir(OWNER, a["id"]))
    remaining = _run(dirs.list_dirs(OWNER))
    assert [row["id"] for row in remaining] == [b["id"], c["id"]]
    assert [row["sortOrder"] for row in remaining] == [0, 1]
    assert remaining[0]["isDefault"] is True


# -- conversations and messages -------------------------------------------


def test_conversation_list_paginates_and_never_crosses_accounts():
    db = FakeDb()
    conversations = ConversationService(db)
    for index in range(3):
        _run(conversations.create_conversation(OWNER, f"c{index}"))
    _run(conversations.create_conversation(OTHER, "theirs"))

    page = _run(conversations.list_conversations(OWNER, 1, 2))
    assert len(page) == 2
    assert all(row["accountId"] == OWNER for row in page)
    assert isinstance(page, list)
    assert len(_run(conversations.list_conversations(OWNER, 2, 2))) == 1


def test_conversation_detail_and_mutations_are_owner_scoped():
    db = FakeDb()
    conversations = ConversationService(db)
    created = _run(conversations.create_conversation(OWNER, "mine"))

    _conversation(db, created["id"], account=OWNER)
    assert _code(conversations.detail(OTHER, created["id"])) == "NOT_FOUND"
    assert _code(conversations.toggle_share(OTHER, created["id"], True)) == "NOT_FOUND"
    assert _code(conversations.clear_messages(OTHER, created["id"])) == "NOT_FOUND"
    assert _code(conversations.delete_conversation(OTHER, created["id"])) == "NOT_FOUND"


def test_conversation_share_token_round_trips_and_requires_is_share():
    db = FakeDb()
    conversations = ConversationService(db)
    created = _run(conversations.create_conversation(OWNER, "shared chat"))
    import base64

    token = base64.b64encode(f"planinc-ai-share-{created['id']}".encode()).decode()
    assert decode_conversation_share_id(token) == created["id"]

    assert _code(conversations.public_detail(token)) == "NOT_FOUND"

    _run(conversations.toggle_share(OWNER, created["id"], True))
    public = _run(conversations.public_detail(token))
    assert public["id"] == created["id"]
    assert public["messages"] == []

    assert _code(conversations.public_detail("not-base64!")) == "BAD_REQUEST"
    assert (
        _code(conversations.public_detail(base64.b64encode(b"other").decode()))
        == "BAD_REQUEST"
    )


def test_message_reads_use_the_owning_conversation_even_for_legacy_rows():
    db = FakeDb()
    messages = MessageService(db)
    _conversation(db, 1, account=OWNER)
    # Legacy rows have no accountId; the conversation is the authority.
    db.seed(
        "message",
        1,
        {
            "conversationId": 1,
            "content": "hi",
            "role": "user",
            "createdAt": "2026-01-01T00:00:00+00:00",
        },
    )

    rows = _run(messages.list_messages(OWNER, 1, 1, 20))
    assert [row["content"] for row in rows] == ["hi"]
    assert _code(messages.list_messages(OTHER, 1, 1, 20)) == "NOT_FOUND"

    created = _run(
        messages.create_message(
            OWNER, {"conversationId": 1, "content": "yo", "role": "user"}
        )
    )
    assert created["conversationId"] == 1
    assert (
        _code(
            messages.create_message(
                OTHER, {"conversationId": 1, "content": "nope", "role": "user"}
            )
        )
        == "NOT_FOUND"
    )
    assert (
        _code(
            messages.create_message(
                OWNER, {"conversationId": 1, "content": "x", "role": "root"}
            )
        )
        == "BAD_REQUEST"
    )


def test_message_delete_removes_the_anchor_and_everything_newer():
    db = FakeDb()
    messages = MessageService(db)
    _conversation(db, 1, account=OWNER)
    for message_id, stamp in enumerate(
        (
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
            "2026-01-01T00:00:02+00:00",
        ),
        start=1,
    ):
        db.seed(
            "message",
            message_id,
            {
                "conversationId": 1,
                "content": f"m{message_id}",
                "role": "user",
                "accountId": OWNER,
                "createdAt": stamp,
            },
        )

    result = _run(messages.delete_message(OWNER, 2))
    assert result == {"success": True}
    assert sorted(db._table("message")) == [1]


def test_clear_after_keeps_older_messages_only():
    db = FakeDb()
    messages = MessageService(db)
    _conversation(db, 1, account=OWNER)
    for message_id, stamp in enumerate(
        (
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
        ),
        start=1,
    ):
        db.seed(
            "message",
            message_id,
            {
                "conversationId": 1,
                "content": f"m{message_id}",
                "role": "user",
                "accountId": OWNER,
                "createdAt": stamp,
            },
        )

    assert _run(messages.clear_after(OWNER, 1)) == {"success": True, "removed": 1}
    assert sorted(db._table("message")) == [1]


# -- follows ---------------------------------------------------------------


def test_site_origin_normalises_and_rejects_relative_urls():
    assert site_origin("https://a.test/path?x=1") == "https://a.test"
    assert _code_text(lambda: site_origin("not-a-url")) == "BAD_REQUEST"


def _code_text(fn) -> str:
    with pytest.raises(DomainError) as caught:
        fn()
    return caught.value.code


def test_follow_is_origin_keyed_and_rejects_a_second_follow():
    db = FakeDb()
    follows = FollowsService(db)
    created = _run(
        follows.follow(
            OWNER,
            {
                "siteUrl": "https://a.test/notes/1",
                "mySiteUrl": "https://me.test",
                "siteName": "A",
            },
        )
    )
    assert created["data"]["siteUrl"] == "https://a.test"

    assert (
        _code(
            follows.follow(
                OWNER,
                {"siteUrl": "https://a.test/other", "mySiteUrl": "https://me.test"},
            )
        )
        == "CONFLICT"
    )
    # A different account may follow the same site.
    _run(
        follows.follow(
            OTHER, {"siteUrl": "https://a.test", "mySiteUrl": "https://other.test"}
        )
    )

    assert _run(follows.unfollow(OWNER, {"siteUrl": "https://a.test/x"})) is True
    assert _run(follows.follow_list(OWNER)) == []
    assert len(_run(follows.follow_list(OTHER))) == 1


# -- share approvals -------------------------------------------------------


def _policy(db: FakeDb, required: bool) -> None:
    db.seed("config", 1, {"key": "requireShareApproval", "config": {"value": required}})


def test_policy_defaults_off_and_set_policy_toggles_the_row():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    assert _run(approvals.policy(OWNER, False)) == {
        "requireShareApproval": False,
        "isAdmin": False,
    }

    assert _run(approvals.set_policy(True)) == {"requireShareApproval": True}
    assert db._table("config")[1]["config"] == {"type": "boolean", "value": True}

    _run(approvals.set_policy(False))
    assert len(db._table("config")) == 1
    assert db._table("config")[1]["config"]["value"] is False


def test_request_internal_skips_self_and_unknown_accounts():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    db.seed("accounts", OWNER, {"name": "owner"})
    db.seed("accounts", OTHER, {"name": "other"})

    created = _run(
        approvals.request_internal(
            OWNER, {"noteId": 1, "accountIds": [OWNER, OTHER, 99], "message": "please"}
        )
    )
    assert [row["inviteeAccountId"] for row in created] == [OTHER]
    assert created[0]["status"] == "pending"
    assert created[0]["requiresAdmin"] is False

    assert (
        _code(approvals.request_internal(OWNER, {"noteId": 1, "accountIds": [99]}))
        == "BAD_REQUEST"
    )
    # Re-requesting reuses the pending row instead of piling up duplicates.
    again = _run(
        approvals.request_internal(OWNER, {"noteId": 1, "accountIds": [OTHER]})
    )
    assert again[0]["id"] == created[0]["id"]
    assert len(db._table("shareApprovals")) == 1


def test_pending_internal_request_never_grants_note_access():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    db.seed("accounts", OTHER, {"name": "other"})
    _policy(db, True)

    created = _run(
        approvals.request_internal(OWNER, {"noteId": 1, "accountIds": [OTHER]})
    )
    assert created[0]["requiresAdmin"] is True
    assert created[0]["adminApproved"] is False
    assert _run(approvals.can_access_note(1, OTHER)) is False
    assert db._table("noteInternalShare") == {}

    # Invitee accepts, but the admin gate keeps it pending.
    accepted = _run(
        approvals.decide(OTHER, False, {"id": created[0]["id"], "approve": True})
    )
    assert accepted["status"] == "pending"
    assert accepted["adminApproved"] is False
    assert _run(approvals.can_access_note(1, OTHER)) is False

    # Admin sign-off completes it and grants access.
    final = _run(
        approvals.decide(OWNER, True, {"id": created[0]["id"], "approve": True})
    )
    assert final["status"] == "approved"
    assert final["adminApproved"] is True
    assert _run(approvals.can_access_note(1, OTHER)) is True


def test_decide_rejects_the_owner_without_admin_and_public_scope():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    db.seed(
        "shareApprovals",
        1,
        {
            "accountId": OWNER,
            "noteId": 1,
            "scope": "internal",
            "status": "pending",
            "inviteeAccountId": OTHER,
            "requiresAdmin": False,
            "adminApproved": True,
        },
    )
    db.seed(
        "shareApprovals",
        2,
        {
            "accountId": OWNER,
            "noteId": 1,
            "scope": "public",
            "status": "pending",
            "requiresAdmin": True,
            "adminApproved": False,
        },
    )

    assert (
        _code(approvals.decide(OWNER, False, {"id": 1, "approve": True})) == "FORBIDDEN"
    )
    assert (
        _code(approvals.decide(OTHER, False, {"id": 2, "approve": True})) == "FORBIDDEN"
    )
    assert (
        _code(approvals.decide(OTHER, False, {"id": 99, "approve": True}))
        == "NOT_FOUND"
    )


def test_public_request_publishes_immediately_when_policy_is_off():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)

    result = _run(
        approvals.request_public(
            OWNER, {"noteId": 1, "password": "pw", "expireAt": None}
        )
    )
    assert result["published"] is True
    assert result["shareUrl"]
    assert result["approval"]["status"] == "approved"
    note = db._table("notes")[1]
    assert note["isShare"] is True
    assert note["sharePassword"] == "pw"


def test_public_request_waits_for_admin_and_keeps_the_staged_password():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    _policy(db, True)

    result = _run(approvals.request_public(OWNER, {"noteId": 1, "password": "pw"}))
    assert result["published"] is False
    assert result["shareUrl"] is None
    assert result["approval"]["status"] == "pending"
    assert db._table("notes")[1]["isShare"] is False
    assert db._table("notes")[1]["sharePassword"] == "pw"

    decided = _run(
        approvals.decide(OWNER, True, {"id": result["approval"]["id"], "approve": True})
    )
    assert decided["status"] == "approved"
    assert db._table("notes")[1]["isShare"] is True
    assert db._table("notes")[1]["sharePassword"] == "pw"


def test_invite_by_email_renders_delivery_and_acceptance_guards():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)

    result = _run(
        approvals.invite_by_email(
            OWNER, {"noteId": 1, "email": "a@b.test", "origin": "https://plan.test/"}
        )
    )
    assert result["delivered"] is False
    assert (
        result["url"] == f"https://plan.test/share/invite/{result['approval']['token']}"
    )
    assert "Quarterly plan" in result["subject"]

    accepted = _run(approvals.accept_invite(OTHER, result["approval"]["token"]))
    assert accepted["status"] == "approved"
    assert accepted["inviteeAccountId"] == OTHER
    assert _run(approvals.can_access_note(1, OTHER)) is True

    assert _code(approvals.accept_invite(OTHER, "missing-token")) == "NOT_FOUND"


def test_expired_and_admin_gated_invites_are_refused():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    db.seed(
        "shareApprovals",
        1,
        {
            "accountId": OWNER,
            "noteId": 1,
            "scope": "email",
            "status": "pending",
            "token": "expired-token",
            "requiresAdmin": False,
            "adminApproved": True,
            "expiresAt": datetime.now(UTC) - timedelta(days=1),
        },
    )
    db.seed(
        "shareApprovals",
        2,
        {
            "accountId": OWNER,
            "noteId": 1,
            "scope": "email",
            "status": "pending",
            "token": "gated-token",
            "requiresAdmin": True,
            "adminApproved": False,
            "expiresAt": datetime.now(UTC) + timedelta(days=1),
        },
    )

    assert _code(approvals.accept_invite(OTHER, "expired-token")) == "BAD_REQUEST"
    assert _code(approvals.accept_invite(OTHER, "gated-token")) == "FORBIDDEN"
    assert _run(approvals.can_access_note(1, OTHER)) is False


def test_revoke_drops_the_internal_share_row_and_is_scoped():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    db.seed("accounts", OTHER, {"name": "other"})
    created = _run(
        approvals.request_internal(OWNER, {"noteId": 1, "accountIds": [OTHER]})
    )
    _run(approvals.decide(OTHER, False, {"id": created[0]["id"], "approve": True}))
    assert _run(approvals.can_access_note(1, OTHER)) is True

    assert _code(approvals.revoke(OTHER, False, created[0]["id"])) == "FORBIDDEN"
    revoked = _run(approvals.revoke(OWNER, False, created[0]["id"]))
    assert revoked["status"] == "revoked"
    assert _run(approvals.can_access_note(1, OTHER)) is False


def test_queues_default_to_pending_and_stay_scoped():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db)
    db.seed("accounts", OTHER, {"name": "other"})
    created = _run(
        approvals.request_internal(OWNER, {"noteId": 1, "accountIds": [OTHER]})
    )

    assert [row["id"] for row in _run(approvals.inbox(OTHER))] == [created[0]["id"]]
    assert _run(approvals.inbox(OTHER, "approved")) == []
    assert [row["id"] for row in _run(approvals.outgoing(OWNER))] == [created[0]["id"]]
    assert _run(approvals.outgoing(OTHER)) == []
    assert [row["id"] for row in _run(approvals.pending_admin())] == [created[0]["id"]]
    assert [row["id"] for row in _run(approvals.for_note(OWNER, 1))] == [
        created[0]["id"]
    ]
    assert _run(approvals.for_note(OTHER, 1)) == []


def test_request_public_cannot_touch_another_accounts_note():
    db = FakeDb()
    approvals = ShareApprovalService(db)
    _note(db, account=OWNER)
    assert _code(approvals.request_public(OTHER, {"noteId": 1})) == "NOT_FOUND"
    assert db._table("notes")[1]["isShare"] is False
