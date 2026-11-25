"""Collaboration domain services (slice S6).

Ports the account-scoped rules of ``server/routerTrpc/{agentDirectory,
conversation,message,follows,shareApproval}.ts``.

Deliberate deviations from the source stack:

- ``follows`` is the federated *site* follow list (``siteUrl`` /
  ``followType``), not a user graph. The TS handlers fetch the followed site's
  ``/api/v1/public/site-info`` and POST back to it; a port must not make an
  outbound request on a write path, so the port stores the row from the caller's
  payload and skips the notification. The row shape is unchanged, so the lists
  stay truthful.
- ``shareApprovals.inviteByEmail`` renders the invite message but does not send
  it: no mail transport is bundled. ``delivered`` is therefore always ``false``
  and the rendered subject/body/url are returned so the UI can copy the link.
- ``shareApprovals`` keeps the layered model: an approval row is created
  ``pending`` and only an ``approved`` row is access-bearing — a pending
  internal request never writes ``noteInternalShare``, so it cannot leak a note.
- ``shareApprovals.decide`` completes the approval as soon as the admin gate is
  satisfied: an admin deciding a public request, or an admin signing off a row
  the recipient already accepted, flips it straight to ``approved``. The source
  server left those rows ``pending`` until a *second* decision, which dead-ends
  a public link (there is no recipient to decide again).
- Reads that the TS server left unscoped (``message.list``,
  ``message.update``, ``message.delete``, ``conversation.clearMessages``) are
  gated on the owning conversation here. Legacy message rows carry no
  ``accountId``, so ownership is resolved through the conversation instead.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from ..db.ids import next_id
from .errors import DomainError
from .store import AccountStore, normalize_record, now
from .users import normalize_id

# Table names follow ``schema.json`` (plural camelCase), like the other ports.
ACCOUNT = "accounts"
AGENT_DIRECTORY = "agentDirectories"
CONVERSATION = "conversation"
MESSAGE = "message"
FOLLOWS = "follows"
SHARE_APPROVAL = "shareApprovals"
NOTE_INTERNAL_SHARE = "noteInternalShare"
CACHE = "cache"
CONFIG = "config"
NOTES = "notes"

SHARE_APPROVAL_POLICY_KEY = "requireShareApproval"
SHARE_SCOPES = ("internal", "email", "public")
SHARE_STATUSES = ("pending", "approved", "rejected", "revoked")
SHARE_ID_PREFIX = "planinc-ai-share-"
INVITE_TTL_DAYS = 14
MESSAGE_ROLES = ("user", "assistant", "system")
AGENT_DIR_KINDS = ("working", "skills")


def _new_token() -> str:
    return secrets.token_urlsafe(24)


def _as_datetime(value: Any) -> datetime | None:
    """Coerce a stored timestamp (datetime | ISO string | None) to UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def site_origin(value: str) -> str:
    """``https://host/some/path`` -> ``https://host`` (TS ``new URL(u).origin``)."""
    text = (value or "").strip()
    if not text:
        raise DomainError("BAD_REQUEST", "siteUrl is required")
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        raise DomainError("BAD_REQUEST", "siteUrl must be an absolute URL")
    return f"{parts.scheme}://{parts.netloc}"


def validate_agent_path(value: str) -> str:
    """Mirror the TS ``agentPath`` schema: non-empty, bounded, no traversal.

    The server never touches these paths — the agent client does — so this is
    input hygiene at the boundary, not a sandbox.
    """
    path = (value or "").strip()
    if not path:
        raise DomainError("BAD_REQUEST", "Path is required")
    if len(path) > 512:
        raise DomainError("BAD_REQUEST", "Path is too long")
    if "\0" in path:
        raise DomainError("BAD_REQUEST", "Path contains a null byte")
    if ".." in [segment for segment in path.replace("\\", "/").split("/") if segment]:
        raise DomainError("BAD_REQUEST", 'Path must not contain ".." segments')
    return path


def derive_agent_dir_label(label: str, path: str) -> str:
    """Mirror ``deriveAgentDirLabel``: fall back to the path's basename."""
    text = (label or "").strip()
    if text:
        return text[:80]
    cleaned = (path or "").rstrip("/\\")
    segments = [part for part in cleaned.replace("\\", "/").split("/") if part]
    return (segments[-1] if segments else cleaned)[:80]


def decode_conversation_share_id(share_id: str) -> int:
    """Decode the ``base64('planinc-ai-share-<id>')`` token the UI builds."""
    raw = (share_id or "").strip()
    if not raw:
        raise DomainError("BAD_REQUEST", "Invalid share link")
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise DomainError("BAD_REQUEST", "Invalid share link") from exc
    if not decoded.startswith(SHARE_ID_PREFIX):
        raise DomainError("BAD_REQUEST", "Invalid share link")
    try:
        return int(decoded[len(SHARE_ID_PREFIX) :])
    except ValueError as exc:
        raise DomainError("BAD_REQUEST", "Invalid share link") from exc


class AgentDirectoryService(AccountStore):
    """``agentDirectories.*`` — working/skills directory entries."""

    def __init__(self, client: Any) -> None:
        super().__init__(client, AGENT_DIRECTORY)

    async def list_dirs(
        self,
        account_id: int,
        *,
        kind: str | None = None,
        include_disabled: bool = False,
    ) -> list[dict]:
        rows = await self.scoped(account_id)
        if kind:
            rows = [row for row in rows if row.get("kind") == kind]
        if not include_disabled:
            rows = [row for row in rows if row.get("enabled", True)]
        rows.sort(
            key=lambda row: (
                row.get("kind") or "",
                row.get("sortOrder") or 0,
                str(row.get("createdAt") or ""),
            )
        )
        return rows

    async def _siblings(self, account_id: int, kind: str) -> list[dict]:
        rows = await self.scoped(account_id, kind=kind)
        rows.sort(
            key=lambda row: (row.get("sortOrder") or 0, str(row.get("createdAt") or ""))
        )
        return rows

    async def _clear_other_defaults(
        self, account_id: int, kind: str, keep_id: Any | None = None
    ) -> None:
        for sibling in await self._siblings(account_id, kind):
            if sibling.get("isDefault") and sibling["id"] != keep_id:
                await self.update(sibling["id"], account_id, {"isDefault": False})

    async def create_dir(self, account_id: int, data: dict) -> dict:
        kind = str(data.get("kind") or "")
        if kind not in AGENT_DIR_KINDS:
            raise DomainError("BAD_REQUEST", "Unknown directory kind")
        path = validate_agent_path(str(data.get("path") or ""))
        siblings = await self._siblings(account_id, kind)
        if any(row.get("path") == path for row in siblings):
            raise DomainError("CONFLICT", "That directory is already configured")

        is_default = bool(data.get("isDefault")) or not siblings
        created = await self.create(
            account_id,
            {
                "kind": kind,
                "label": derive_agent_dir_label(str(data.get("label") or ""), path),
                "path": path,
                "isDefault": is_default,
                "enabled": bool(data.get("enabled", True)),
                "sortOrder": len(siblings),
            },
        )
        if is_default:
            await self._clear_other_defaults(account_id, kind, keep_id=created["id"])
        return created

    async def update_dir(self, account_id: int, data: dict) -> dict:
        current = await self.get(data.get("id"), account_id)
        kind = str(data.get("kind") or current.get("kind") or "")
        if kind not in AGENT_DIR_KINDS:
            raise DomainError("BAD_REQUEST", "Unknown directory kind")

        patch = {
            key: data[key]
            for key in ("kind", "label", "path", "isDefault", "enabled", "sortOrder")
            if key in data
        }
        next_path = validate_agent_path(
            str(patch.get("path") or current.get("path") or "")
        )
        if next_path != current.get("path"):
            for sibling in await self._siblings(account_id, kind):
                if sibling["id"] != current["id"] and sibling.get("path") == next_path:
                    raise DomainError(
                        "CONFLICT", "That directory is already configured"
                    )
        patch["path"] = next_path
        patch["label"] = derive_agent_dir_label(
            str(patch.get("label", current.get("label")) or ""), next_path
        )
        if not patch:
            return current

        updated = await self.update(current["id"], account_id, patch)
        if patch.get("isDefault") and not current.get("isDefault"):
            await self._clear_other_defaults(account_id, kind, keep_id=current["id"])
        return updated

    async def delete_dir(self, account_id: int, directory_id: Any) -> dict:
        current = await self.get(directory_id, account_id)
        await self.delete(current["id"], account_id)
        remaining = await self._siblings(account_id, str(current.get("kind") or ""))
        for index, sibling in enumerate(remaining):
            patch: dict = {}
            if sibling.get("sortOrder") != index:
                patch["sortOrder"] = index
            if current.get("isDefault") and index == 0 and not sibling.get("isDefault"):
                patch["isDefault"] = True
            if patch:
                await self.update(sibling["id"], account_id, patch)
        return {"success": True}

    async def reorder(
        self, account_id: int, kind: str, ordered_ids: list
    ) -> list[dict]:
        if kind not in AGENT_DIR_KINDS:
            raise DomainError("BAD_REQUEST", "Unknown directory kind")
        rows = await self._siblings(account_id, kind)
        known = {row["id"] for row in rows}
        ordered = [normalize_id(item) for item in ordered_ids or []]
        for index, record_id in enumerate([rid for rid in ordered if rid in known]):
            row = next(row for row in rows if row["id"] == record_id)
            if row.get("sortOrder") != index:
                await self.update(record_id, account_id, {"sortOrder": index})
        return await self._siblings(account_id, kind)


class ConversationService(AccountStore):
    """``conversation.*`` — AI chat threads."""

    def __init__(self, client: Any) -> None:
        super().__init__(client, CONVERSATION)

    async def create_conversation(self, account_id: int, title: str | None) -> dict:
        return await self.create(account_id, {"title": title or "", "isShare": False})

    async def list_conversations(
        self, account_id: int, page: int = 1, size: int = 20
    ) -> list[dict]:
        rows = await self.scoped(account_id)
        rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        start = max(page - 1, 0) * max(size, 0)
        return rows[start : start + size]

    async def update_title(
        self, account_id: int, conversation_id: Any, title: str | None
    ) -> dict:
        patch = {"title": title} if title is not None else {}
        return await self.update(conversation_id, account_id, patch)

    async def detail(self, account_id: int, conversation_id: Any) -> dict:
        conversation = await self.get(conversation_id, account_id)
        messages = [
            row
            for row in await self._messages()
            if row.get("conversationId") == conversation["id"]
        ]
        messages.sort(key=lambda row: str(row.get("createdAt") or ""))
        return {**conversation, "messages": messages}

    async def public_detail(self, share_id: str) -> dict | None:
        conversation_id = decode_conversation_share_id(share_id)
        for row in await self.all():
            if row["id"] != conversation_id or not row.get("isShare"):
                continue
            messages = [
                message
                for message in await self._messages()
                if message.get("conversationId") == row["id"]
            ]
            messages.sort(key=lambda message: str(message.get("createdAt") or ""))
            return {**row, "messages": messages}
        raise DomainError("NOT_FOUND", "Conversation not found or not shared")

    async def toggle_share(
        self, account_id: int, conversation_id: Any, is_share: bool
    ) -> dict:
        return await self.update(
            conversation_id, account_id, {"isShare": bool(is_share)}
        )

    async def clear_messages(self, account_id: int, conversation_id: Any) -> dict:
        conversation = await self.get(conversation_id, account_id)
        for row in await self._messages():
            if row.get("conversationId") == conversation["id"]:
                await self._client.delete(f"{MESSAGE}:{row['id']}")
        return {"success": True}

    async def delete_conversation(self, account_id: int, conversation_id: Any) -> dict:
        conversation = await self.get(conversation_id, account_id)
        for row in await self._messages():
            if row.get("conversationId") == conversation["id"]:
                await self._client.delete(f"{MESSAGE}:{row['id']}")
        await self.delete(conversation["id"], account_id)
        return {"success": True}

    async def _messages(self) -> list[dict]:
        return [normalize_record(row) for row in await self._client.select(MESSAGE)]


class MessageService(AccountStore):
    """``message.*`` — messages inside a conversation."""

    def __init__(self, client: Any) -> None:
        super().__init__(client, MESSAGE)

    async def _owned_conversation(self, conversation_id: Any, account_id: int) -> dict:
        target = normalize_id(conversation_id)
        for row in await self._client.select(CONVERSATION):
            record = normalize_record(row)
            if record["id"] == target and record.get("accountId") == account_id:
                return record
        raise DomainError("NOT_FOUND", "Conversation not found")

    async def _owned_message(self, message_id: Any, account_id: int) -> dict:
        message = await self.by_id(message_id)
        if message is None:
            raise DomainError("NOT_FOUND", "Message not found")
        # Messages created before the port carry no ``accountId``; the owning
        # conversation is the authority either way.
        await self._owned_conversation(message.get("conversationId"), account_id)
        return message

    async def list_messages(
        self,
        account_id: int,
        conversation_id: Any,
        page: int = 1,
        size: int = 20,
    ) -> list[dict]:
        conversation = await self._owned_conversation(conversation_id, account_id)
        rows = [
            row
            for row in await self.all()
            if row.get("conversationId") == conversation["id"]
        ]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""))
        start = max(page - 1, 0) * max(size, 0)
        return rows[start : start + size]

    async def create_message(self, account_id: int, data: dict) -> dict:
        conversation = await self._owned_conversation(
            data.get("conversationId"), account_id
        )
        role = str(data.get("role") or "user")
        if role not in MESSAGE_ROLES:
            raise DomainError("BAD_REQUEST", "Unknown message role")
        return await self.create(
            account_id,
            {
                "conversationId": conversation["id"],
                "content": data.get("content") or "",
                "role": role,
                "metadata": data.get("metadata"),
            },
        )

    async def update_message(self, account_id: int, data: dict) -> dict:
        message = await self._owned_message(data.get("id"), account_id)
        return await self.update(
            message["id"], account_id, {"content": data.get("content") or ""}
        )

    async def clear_after(self, account_id: int, message_id: Any) -> dict:
        anchor = await self._owned_message(message_id, account_id)
        cutoff = str(anchor.get("createdAt") or "")
        removed = 0
        anchor_conversation = anchor["conversationId"]
        for row in await self.all():
            if row.get("conversationId") != anchor_conversation:
                continue
            if str(row.get("createdAt") or "") > cutoff:
                await self._client.delete(f"{MESSAGE}:{row['id']}")
                removed += 1
        return {"success": True, "removed": removed}

    async def delete_message(self, account_id: int, message_id: Any) -> dict:
        """Delete one message and everything newer in its conversation."""
        anchor = await self._owned_message(message_id, account_id)
        await self.clear_after(account_id, anchor["id"])
        await self._client.delete(f"{MESSAGE}:{anchor['id']}")
        return {"success": True}


class FollowsService(AccountStore):
    """``follows.*`` — federated site follow list."""

    def __init__(self, client: Any) -> None:
        super().__init__(client, FOLLOWS)

    async def follow(self, account_id: int, data: dict) -> dict:
        site_url = site_origin(str(data.get("siteUrl") or ""))
        # ``mySiteUrl`` is validated for shape only: it would be sent to the
        # followed site, and a write path must not make outbound calls.
        if data.get("mySiteUrl"):
            site_origin(str(data.get("mySiteUrl")))
        existing = await self.scoped(
            account_id, followType="following", siteUrl=site_url
        )
        if existing:
            raise DomainError("CONFLICT", "Already following this site")
        row = await self.create(
            account_id,
            {
                "siteUrl": site_url,
                "siteName": data.get("siteName") or "",
                "siteAvatar": data.get("siteAvatar") or "",
                "description": data.get("description") or "",
                "followType": "following",
            },
        )
        return {"success": True, "data": row}

    async def unfollow(self, account_id: int, data: dict) -> bool:
        site_url = site_origin(str(data.get("siteUrl") or ""))
        for row in await self.scoped(
            account_id, followType="following", siteUrl=site_url
        ):
            await self._client.delete(f"{FOLLOWS}:{row['id']}")
        return True

    async def follow_list(
        self, account_id: int, user_id: Any | None = None
    ) -> list[dict]:
        target = normalize_id(user_id) or account_id
        rows = await self.scoped(target, followType="following")
        return [_clean_follow(row) for row in rows]

    async def follower_list(
        self, account_id: int, user_id: Any | None = None
    ) -> list[dict]:
        target = normalize_id(user_id) or account_id
        rows = await self.scoped(target, followType="follower")
        return [_clean_follow(row) for row in rows]

    async def recommand_list(self, account_id: int, search: str = "") -> list[dict]:
        for row in await self._client.select(CACHE):
            if row.get("key") != "recommand_list":
                continue
            value = row.get("value") or {}
            items = value.get(str(account_id)) or []
            filtered = [
                item for item in items if search in str(item.get("content") or "")
            ]
            filtered.sort(
                key=lambda item: str(item.get("updatedAt") or ""), reverse=True
            )
            return filtered
        return []


def _clean_follow(row: dict) -> dict:
    record = dict(row)
    for key in ("siteName", "siteAvatar", "description"):
        if record.get(key) is None:
            record.pop(key, None)
    return record


class ShareApprovalService(AccountStore):
    """``shareApprovals.*`` — layered share approvals (internal/email/public)."""

    def __init__(self, client: Any) -> None:
        super().__init__(client, SHARE_APPROVAL)

    # -- policy -----------------------------------------------------------

    async def _policy_required(self) -> bool:
        for row in await self._client.select(CONFIG):
            if row.get("key") != SHARE_APPROVAL_POLICY_KEY:
                continue
            config = row.get("config") or {}
            value = config.get("value") if isinstance(config, dict) else config
            return value is True or value == "true"
        return False

    async def policy(self, account_id: int, is_admin: bool) -> dict:
        return {
            "requireShareApproval": await self._policy_required(),
            "isAdmin": bool(is_admin),
        }

    async def set_policy(self, required: bool) -> dict:
        for row in await self._client.select(CONFIG):
            if row.get("key") == SHARE_APPROVAL_POLICY_KEY:
                await self._client.update(
                    f"{CONFIG}:{normalize_id(row.get('id'))}",
                    {"config": {"type": "boolean", "value": bool(required)}},
                )
                return {"requireShareApproval": bool(required)}
        new_id = await next_id(self._client, CONFIG)
        await self._client.create(
            f"{CONFIG}:{new_id}",
            {
                "key": SHARE_APPROVAL_POLICY_KEY,
                "config": {"type": "boolean", "value": bool(required)},
            },
        )
        return {"requireShareApproval": bool(required)}

    # -- reads ------------------------------------------------------------

    async def inbox(
        self, account_id: int, status: str | None = "pending"
    ) -> list[dict]:
        wanted = status or "pending"
        rows = [
            row
            for row in await self.all()
            if normalize_id(row.get("inviteeAccountId")) == account_id
            and row.get("status") == wanted
        ]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        return rows

    async def outgoing(
        self,
        account_id: int,
        note_id: Any | None = None,
        status: str | None = None,
    ) -> list[dict]:
        rows = [
            row
            for row in await self.all()
            if normalize_id(row.get("accountId")) == account_id
        ]
        if note_id is not None:
            target = normalize_id(note_id)
            rows = [row for row in rows if normalize_id(row.get("noteId")) == target]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        return rows

    async def pending_admin(self, status: str | None = "pending") -> list[dict]:
        wanted = status or "pending"
        rows = [row for row in await self.all() if row.get("status") == wanted]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""))
        return rows

    async def for_note(self, account_id: int, note_id: Any) -> list[dict]:
        target = normalize_id(note_id)
        rows = [
            row
            for row in await self.all()
            if normalize_id(row.get("noteId")) == target
            and normalize_id(row.get("accountId")) == account_id
        ]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        return rows

    # -- requests ---------------------------------------------------------

    async def _owned_note(self, note_id: Any, account_id: int) -> dict:
        target = normalize_id(note_id)
        for raw in await self._client.select(NOTES):
            record = normalize_record(raw)
            if record["id"] == target and record.get("accountId") == account_id:
                return record
        raise DomainError("NOT_FOUND", "Note not found")

    async def _known_accounts(self, account_ids: list) -> list[int]:
        known: list[int] = []
        rows = [normalize_record(row) for row in await self._client.select(ACCOUNT)]
        for candidate in account_ids or []:
            target = normalize_id(candidate)
            if target is not None and any(row["id"] == target for row in rows):
                known.append(target)
        return known

    async def _upsert_approval(
        self, account_id: int, fields: dict, match: dict
    ) -> dict:
        for row in await self.all():
            if all(row.get(key) == value for key, value in match.items()):
                if (
                    row.get("status") == "approved"
                    and fields.get("status") != "approved"
                ):
                    return row
                return await self.update(
                    row["id"], normalize_id(row.get("accountId")) or account_id, fields
                )
        return await self.create(account_id, fields)

    async def _approval_fields(
        self, account_id: int, note_id: int, **overrides: Any
    ) -> dict:
        required = await self._policy_required()
        fields = {
            "noteId": note_id,
            "scope": "internal",
            "status": "pending",
            "inviteeAccountId": None,
            "inviteeEmail": None,
            "token": _new_token(),
            "canEdit": True,
            "requiresAdmin": required,
            "adminApproved": not required,
            "requestedBy": account_id,
            "decidedBy": None,
            "decidedAt": None,
            "decisionNote": None,
            "expiresAt": None,
            "message": "",
        }
        fields.update(overrides)
        return fields

    async def request_internal(self, account_id: int, data: dict) -> list[dict]:
        note = await self._owned_note(data.get("noteId"), account_id)
        requested = [
            item
            for item in data.get("accountIds") or []
            if normalize_id(item) != account_id
        ]
        targets = await self._known_accounts(requested)
        if not targets:
            raise DomainError("BAD_REQUEST", "No matching accounts")
        can_edit = bool(data.get("canEdit", True))
        message = str(data.get("message") or "")[:280]
        created: list[dict] = []
        for invitee in targets:
            fields = await self._approval_fields(
                account_id,
                note["id"],
                scope="internal",
                inviteeAccountId=invitee,
                canEdit=can_edit,
                message=message,
            )
            created.append(
                await self._upsert_approval(
                    account_id,
                    fields,
                    {
                        "noteId": note["id"],
                        "scope": "internal",
                        "inviteeAccountId": invitee,
                    },
                )
            )
        return created

    async def invite_by_email(self, account_id: int, data: dict) -> dict:
        note = await self._owned_note(data.get("noteId"), account_id)
        email = str(data.get("email") or "").strip()
        if "@" not in email:
            raise DomainError("BAD_REQUEST", "A valid email is required")
        origin = str(data.get("origin") or "").strip().rstrip("/")
        if not origin:
            raise DomainError("BAD_REQUEST", "origin is required")
        days = int(data.get("expiryDays") or INVITE_TTL_DAYS)
        fields = await self._approval_fields(
            account_id,
            note["id"],
            scope="email",
            status="pending",
            inviteeEmail=email,
            canEdit=bool(data.get("canEdit", True)),
            message=str(data.get("message") or "")[:280],
            expiresAt=now() + timedelta(days=max(days, 1)),
        )
        approval = await self._upsert_approval(
            account_id,
            fields,
            {"noteId": note["id"], "scope": "email", "inviteeEmail": email},
        )
        token = str(approval.get("token") or fields["token"])
        url = f"{origin}/share/invite/{token}"
        title = str(note.get("content") or "").split("\n")[0][:80] or "a note"
        body = "\n".join(
            [
                f'A teammate would like to share "{title}" with you.',
                fields["message"] and f"\nMessage: {fields['message']}" or "",
                f"\nApprove the share: {url}",
                f"\nThis invitation expires in {days} days.",
            ]
        )
        return {
            "approval": approval,
            "delivered": False,
            "subject": f'PlanInc share: "{title}"',
            "body": body,
            "url": url,
        }

    async def request_public(self, account_id: int, data: dict) -> dict:
        note = await self._owned_note(data.get("noteId"), account_id)
        required = await self._policy_required()
        password = data.get("password")
        expire_at = _as_datetime(data.get("expireAt"))
        if not required:
            share_id = await self._publish(note["id"], password, expire_at)
            approval = await self.create(
                account_id,
                await self._approval_fields(
                    account_id,
                    note["id"],
                    scope="public",
                    status="approved",
                    requiresAdmin=False,
                    adminApproved=True,
                    canEdit=False,
                    decidedBy=account_id,
                    decidedAt=now(),
                    message=str(data.get("message") or "")[:280],
                    expiresAt=expire_at,
                ),
            )
            return {"approval": approval, "published": True, "shareUrl": share_id}

        approval = await self.create(
            account_id,
            await self._approval_fields(
                account_id,
                note["id"],
                scope="public",
                status="pending",
                requiresAdmin=True,
                adminApproved=False,
                canEdit=False,
                message=str(data.get("message") or "")[:280],
                expiresAt=expire_at,
            ),
        )
        if password:
            # Stage the requested password so an admin approval keeps it.
            await self._client.update(
                f"{NOTES}:{note['id']}",
                {"sharePassword": password, "shareExpiryDate": expire_at},
            )
        return {"approval": approval, "published": False, "shareUrl": None}

    # -- decisions --------------------------------------------------------

    async def _publish(
        self, note_id: int, password: Any = None, expire_at: datetime | None = None
    ) -> str | None:
        for raw in await self._client.select(NOTES):
            record = normalize_record(raw)
            if record["id"] != note_id:
                continue
            share_id = record.get("shareEncryptedUrl") or _new_token()[:12]
            # ``None`` keeps whatever password the note already has; only an
            # explicit string replaces it.
            next_password = (
                record.get("sharePassword") or "" if password is None else password
            )
            await self._client.update(
                f"{NOTES}:{note_id}",
                {
                    "isShare": True,
                    "shareEncryptedUrl": share_id,
                    "sharePassword": next_password,
                    "shareExpiryDate": expire_at,
                },
            )
            return share_id
        return None

    async def decide(self, account_id: int, is_admin: bool, data: dict) -> dict:
        approval = await self.by_id(data.get("id"))
        if approval is None:
            raise DomainError("NOT_FOUND", "Share request not found")
        approve = bool(data.get("approve"))
        is_invitee = normalize_id(approval.get("inviteeAccountId")) == account_id
        is_owner = normalize_id(approval.get("accountId")) == account_id
        if not is_admin and not is_invitee:
            raise DomainError("FORBIDDEN", "You cannot decide this share request")
        if is_owner and not is_admin:
            raise DomainError(
                "FORBIDDEN", "Only the recipient or an administrator can decide"
            )
        if approval.get("scope") == "public" and not is_admin:
            raise DomainError(
                "FORBIDDEN", "Only an administrator can decide a public share"
            )

        owner_id = normalize_id(approval.get("accountId")) or account_id
        waiting_on_admin = approval.get("requiresAdmin") and not approval.get(
            "adminApproved"
        )
        # ``decidedBy == inviteeAccountId`` on a pending row means the recipient
        # already accepted and only the workspace sign-off is left. The source
        # server left that row pending no matter who decided next, which dead-
        # ends an admin decision after the recipient accepted; completing it here
        # is the one intentional behaviour fix in this slice.
        recipient_accepted = approval.get(
            "inviteeAccountId"
        ) is not None and normalize_id(approval.get("decidedBy")) == normalize_id(
            approval.get("inviteeAccountId")
        )
        completes_admin_gate = is_admin and (
            approval.get("scope") == "public" or recipient_accepted
        )
        if approve and waiting_on_admin and not completes_admin_gate:
            # One side accepted; the other still has to. Only an admin can move
            # ``adminApproved``, and a pending row never grants access.
            return await self.update(
                approval["id"],
                owner_id,
                {
                    "status": "pending",
                    "adminApproved": bool(is_admin),
                    "decidedBy": account_id,
                    "decisionNote": str(data.get("decisionNote") or "")[:280],
                },
            )

        updated = await self.update(
            approval["id"],
            owner_id,
            {
                "status": "approved" if approve else "rejected",
                "adminApproved": True if is_admin else approval.get("adminApproved"),
                "decidedBy": account_id,
                "decidedAt": now(),
                "decisionNote": str(data.get("decisionNote") or "")[:280],
            },
        )
        if approve:
            if approval.get("scope") == "internal" and approval.get("inviteeAccountId"):
                await self._grant_internal_share(
                    approval["noteId"],
                    approval["inviteeAccountId"],
                    approval.get("canEdit", True),
                )
            if approval.get("scope") == "public":
                await self._publish(
                    normalize_id(approval["noteId"]),
                    None,
                    _as_datetime(approval.get("expiresAt")),
                )
        return updated

    async def accept_invite(self, account_id: int, token: str) -> dict:
        for row in await self.all():
            if row.get("token") != token or row.get("scope") != "email":
                continue
            if row.get("status") in ("rejected", "revoked"):
                raise DomainError("BAD_REQUEST", "This invitation is no longer valid")
            expires = _as_datetime(row.get("expiresAt"))
            if expires is not None and expires < datetime.now(UTC):
                raise DomainError("BAD_REQUEST", "This invitation has expired")
            if row.get("requiresAdmin") and not row.get("adminApproved"):
                raise DomainError(
                    "FORBIDDEN",
                    "This invitation is still waiting for administrator approval",
                )
            owner_id = normalize_id(row.get("accountId")) or account_id
            updated = await self.update(
                row["id"],
                owner_id,
                {
                    "status": "approved",
                    "inviteeAccountId": account_id,
                    "decidedBy": account_id,
                    "decidedAt": now(),
                },
            )
            await self._grant_internal_share(
                row["noteId"], account_id, row.get("canEdit", True)
            )
            return updated
        raise DomainError("NOT_FOUND", "Invitation not found")

    async def revoke(self, account_id: int, is_admin: bool, approval_id: Any) -> dict:
        approval = await self.by_id(approval_id)
        if approval is None:
            raise DomainError("NOT_FOUND", "Share request not found")
        owner_id = normalize_id(approval.get("accountId")) or account_id
        if owner_id != account_id and not is_admin:
            raise DomainError("FORBIDDEN", "You cannot revoke this share")
        if (
            approval.get("scope") == "internal"
            and approval.get("inviteeAccountId")
            and approval.get("status") == "approved"
        ):
            await self._drop_internal_share(
                approval["noteId"], approval["inviteeAccountId"]
            )
        return await self.update(
            approval["id"], owner_id, {"status": "revoked", "decidedAt": now()}
        )

    # -- internal share rows ---------------------------------------------

    async def _grant_internal_share(
        self, note_id: Any, invitee: Any, can_edit: bool = True
    ) -> None:
        target_note = normalize_id(note_id)
        target_account = normalize_id(invitee)
        if target_note is None or target_account is None:
            return
        for row in await self._client.select(NOTE_INTERNAL_SHARE):
            if (
                normalize_id(row.get("noteId")) == target_note
                and normalize_id(row.get("accountId")) == target_account
            ):
                await self._client.update(
                    f"{NOTE_INTERNAL_SHARE}:{normalize_id(row.get('id'))}",
                    {"canEdit": bool(can_edit)},
                )
                return
        new_id = await next_id(self._client, NOTE_INTERNAL_SHARE)
        await self._client.create(
            f"{NOTE_INTERNAL_SHARE}:{new_id}",
            {
                "noteId": target_note,
                "accountId": target_account,
                "canEdit": bool(can_edit),
            },
        )

    async def _drop_internal_share(self, note_id: Any, invitee: Any) -> None:
        target_note = normalize_id(note_id)
        target_account = normalize_id(invitee)
        for row in await self._client.select(NOTE_INTERNAL_SHARE):
            if (
                normalize_id(row.get("noteId")) == target_note
                and normalize_id(row.get("accountId")) == target_account
            ):
                await self._client.delete(
                    f"{NOTE_INTERNAL_SHARE}:{normalize_id(row.get('id'))}"
                )

    # -- access checks used by notes -------------------------------------

    async def can_access_note(self, note_id: Any, account_id: int) -> bool:
        """Whether ``account_id`` holds an approved internal share of the note."""
        target = normalize_id(note_id)
        for row in await self._client.select(NOTE_INTERNAL_SHARE):
            if (
                normalize_id(row.get("noteId")) == target
                and normalize_id(row.get("accountId")) == account_id
            ):
                return True
        return False
