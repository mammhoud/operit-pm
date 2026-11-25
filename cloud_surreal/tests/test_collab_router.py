"""S6 router surface: registration, auth and cross-account isolation."""

import base64
import json

from robyn import TestClient as RobynTestClient

from app.auth.jwt import issue_api_token
from app.config import Settings
from app.main import build_trpc_router, create_app
from app.services import AppServices
from tests.fakes import FakeDb

SECRET = "collab-secret-that-is-long-enough!"
SETTINGS = Settings(
    db_file="/tmp/collab.db",
    db_ns="planinc",
    db_name="planinc",
    jwt_secret=SECRET,
    port=1111,
)

# The 34 symbols the frontend calls; see ``route-matrix.json`` slice S6.
S6_PROCEDURES = {
    "agentDirectories.create",
    "agentDirectories.delete",
    "agentDirectories.list",
    "agentDirectories.reorder",
    "agentDirectories.update",
    "conversation.clearMessages",
    "conversation.create",
    "conversation.delete",
    "conversation.detail",
    "conversation.list",
    "conversation.publicDetail",
    "conversation.toggleShare",
    "conversation.update",
    "follows.follow",
    "follows.followList",
    "follows.followerList",
    "follows.recommandList",
    "follows.unfollow",
    "message.clearAfter",
    "message.create",
    "message.delete",
    "message.update",
    "shareApprovals.acceptInvite",
    "shareApprovals.decide",
    "shareApprovals.forNote",
    "shareApprovals.inbox",
    "shareApprovals.inviteByEmail",
    "shareApprovals.outgoing",
    "shareApprovals.pendingAdmin",
    "shareApprovals.policy",
    "shareApprovals.requestInternal",
    "shareApprovals.requestPublic",
    "shareApprovals.revoke",
    "shareApprovals.setPolicy",
}


def _setup() -> tuple[RobynTestClient, FakeDb]:
    db = FakeDb()
    services = AppServices.from_client(db, SECRET)
    app = create_app(SETTINGS, router=build_trpc_router(services), services=services)
    return RobynTestClient(app), db


def _token(account_id: int, role: str = "user") -> str:
    return issue_api_token(SECRET, account_id, f"user{account_id}", role)


def _post(client, path: str, payload: dict, token: str | None = None):
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return client.post(f"/api/trpc/{path}", body=json.dumps(payload), headers=headers)


def _data(response):
    return json.loads(response.text)["result"]["data"]["json"]


def _code(response) -> str:
    return json.loads(response.text)["error"]["json"]["data"]["code"]


def test_every_s6_procedure_is_registered():
    services = AppServices.from_client(FakeDb(), SECRET)
    registered = set(build_trpc_router(services).names)
    assert S6_PROCEDURES - registered == set()


def test_collaboration_procedures_require_a_session():
    client, _ = _setup()
    assert _code(
        _post(client, "conversation.list", {"json": {"page": 1, "size": 10}})
    ) == ("UNAUTHORIZED")
    assert _code(_post(client, "shareApprovals.policy", {"json": {}})) == "UNAUTHORIZED"
    assert _code(_post(client, "agentDirectories.list", {"json": {}})) == "UNAUTHORIZED"


def test_conversation_detail_never_crosses_accounts():
    client, _ = _setup()
    owner = _token(1)
    stranger = _token(2)

    created = _data(
        _post(client, "conversation.create", {"json": {"title": "mine"}}, owner)
    )
    assert created["title"] == "mine"

    assert (
        _code(
            _post(
                client, "conversation.detail", {"json": {"id": created["id"]}}, stranger
            )
        )
        == "NOT_FOUND"
    )
    assert (
        _code(
            _post(
                client,
                "conversation.toggleShare",
                {"json": {"id": created["id"], "isShare": True}},
                stranger,
            )
        )
        == "NOT_FOUND"
    )

    assert _data(
        _post(client, "conversation.list", {"json": {"page": 1, "size": 5}}, owner)
    )
    assert (
        _data(
            _post(
                client, "conversation.list", {"json": {"page": 1, "size": 5}}, stranger
            )
        )
        == []
    )


def test_shared_conversation_is_readable_without_a_token():
    client, _ = _setup()
    owner = _token(1)
    created = _data(
        _post(client, "conversation.create", {"json": {"title": "public chat"}}, owner)
    )
    _data(
        _post(
            client,
            "conversation.toggleShare",
            {"json": {"id": created["id"], "isShare": True}},
            owner,
        )
    )
    share_id = base64.b64encode(f"planinc-ai-share-{created['id']}".encode()).decode()

    public = _data(
        _post(client, "conversation.publicDetail", {"json": {"shareId": share_id}})
    )
    assert public["id"] == created["id"]
    assert public["messages"] == []

    assert (
        _code(
            _post(client, "conversation.publicDetail", {"json": {"shareId": "garbage"}})
        )
        == "BAD_REQUEST"
    )


def test_agent_directories_surface_validation_and_conflicts():
    client, _ = _setup()
    token = _token(1)
    created = _data(
        _post(
            client,
            "agentDirectories.create",
            {"json": {"kind": "working", "path": "/repos/a"}},
            token,
        )
    )
    assert created["label"] == "a"

    assert (
        _code(
            _post(
                client,
                "agentDirectories.create",
                {"json": {"kind": "working", "path": "/repos/a"}},
                token,
            )
        )
        == "CONFLICT"
    )
    assert (
        _code(
            _post(
                client,
                "agentDirectories.create",
                {"json": {"kind": "elsewhere", "path": "/x"}},
                token,
            )
        )
        == "BAD_REQUEST"
    )


def test_message_endpoints_require_the_owning_conversation():
    client, _ = _setup()
    owner = _token(1)
    stranger = _token(2)
    conversation = _data(
        _post(client, "conversation.create", {"json": {"title": "chat"}}, owner)
    )

    message = _data(
        _post(
            client,
            "message.create",
            {
                "json": {
                    "conversationId": conversation["id"],
                    "content": "hello",
                    "role": "user",
                }
            },
            owner,
        )
    )
    listed = _data(
        _post(
            client,
            "message.list",
            {"json": {"conversationId": conversation["id"], "page": 1, "size": 10}},
            owner,
        )
    )
    assert [row["content"] for row in listed] == ["hello"]

    assert (
        _code(
            _post(
                client,
                "message.list",
                {"json": {"conversationId": conversation["id"], "page": 1, "size": 10}},
                stranger,
            )
        )
        == "NOT_FOUND"
    )
    assert (
        _code(
            _post(client, "message.delete", {"json": {"id": message["id"]}}, stranger)
        )
        == "NOT_FOUND"
    )


def test_follows_reject_relative_site_urls():
    client, _ = _setup()
    assert (
        _code(
            _post(
                client,
                "follows.follow",
                {"json": {"siteUrl": "not-a-url", "mySiteUrl": "https://me.test"}},
                _token(1),
            )
        )
        == "BAD_REQUEST"
    )


def test_admin_queues_and_policy_are_superadmin_only():
    client, _ = _setup()
    user = _token(1, "user")
    admin = _token(99, "superadmin")

    assert _code(_post(client, "shareApprovals.pendingAdmin", {"json": {}}, user)) == (
        "FORBIDDEN"
    )
    assert (
        _code(
            _post(
                client,
                "shareApprovals.setPolicy",
                {"json": {"requireShareApproval": True}},
                user,
            )
        )
        == "FORBIDDEN"
    )

    assert (
        _data(_post(client, "shareApprovals.pendingAdmin", {"json": {}}, admin)) == []
    )
    assert _data(
        _post(
            client,
            "shareApprovals.setPolicy",
            {"json": {"requireShareApproval": True}},
            admin,
        )
    ) == {"requireShareApproval": True}
    assert _data(_post(client, "shareApprovals.policy", {"json": {}}, user)) == {
        "requireShareApproval": True,
        "isAdmin": False,
    }
