"""``agentDirectories.*``, ``conversation.*``, ``message.*``, ``follows.*`` and
``shareApprovals.*`` procedures (slice S6).

Mirrors ``server/routerTrpc/{agentDirectory,conversation,message,follows,
shareApproval}.ts``. Authorization is resolved from the JWT claims only; every
lookup then re-checks ownership inside the service, so a caller-supplied id can
never reach another account's row.
"""

from __future__ import annotations

from typing import Any

from ..auth.jwt import TokenClaims
from ..domain import policies
from ..domain.collab import (
    AgentDirectoryService,
    ConversationService,
    FollowsService,
    MessageService,
    ShareApprovalService,
)
from ..transport.trpc import Router


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _role(claims: TokenClaims | None) -> str:
    return policies.require_authenticated(claims).role or ""


def _is_admin(claims: TokenClaims | None) -> bool:
    return _role(claims) == "superadmin"


def register_collab_routers(
    router: Router,
    agent_directories: AgentDirectoryService,
    conversations: ConversationService,
    messages: MessageService,
    follows: FollowsService,
    share_approvals: ShareApprovalService,
) -> None:
    # -- agentDirectories -------------------------------------------------

    @router.procedure("agentDirectories.list")
    async def list_agent_dirs(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await agent_directories.list_dirs(
            policies.current_account_id(claims),
            kind=input_.get("kind"),
            include_disabled=bool(input_.get("includeDisabled")),
        )

    @router.procedure("agentDirectories.create")
    async def create_agent_dir(input_: dict, claims: TokenClaims | None) -> dict:
        return await agent_directories.create_dir(
            policies.current_account_id(claims), input_
        )

    @router.procedure("agentDirectories.update")
    async def update_agent_dir(input_: dict, claims: TokenClaims | None) -> dict:
        return await agent_directories.update_dir(
            policies.current_account_id(claims), input_
        )

    @router.procedure("agentDirectories.delete")
    async def delete_agent_dir(input_: dict, claims: TokenClaims | None) -> dict:
        return await agent_directories.delete_dir(
            policies.current_account_id(claims), input_.get("id")
        )

    @router.procedure("agentDirectories.reorder")
    async def reorder_agent_dirs(
        input_: dict, claims: TokenClaims | None
    ) -> list[dict]:
        return await agent_directories.reorder(
            policies.current_account_id(claims),
            str(input_.get("kind") or ""),
            input_.get("orderedIds") or [],
        )

    # -- conversation -----------------------------------------------------

    @router.procedure("conversation.create")
    async def create_conversation(input_: dict, claims: TokenClaims | None) -> dict:
        return await conversations.create_conversation(
            policies.current_account_id(claims), input_.get("title")
        )

    @router.procedure("conversation.list")
    async def list_conversations(
        input_: dict, claims: TokenClaims | None
    ) -> list[dict]:
        return await conversations.list_conversations(
            policies.current_account_id(claims),
            _int(input_.get("page"), 1),
            _int(input_.get("size"), 20),
        )

    @router.procedure("conversation.detail")
    async def conversation_detail(input_: dict, claims: TokenClaims | None) -> dict:
        return await conversations.detail(
            policies.current_account_id(claims), input_.get("id")
        )

    @router.procedure("conversation.publicDetail")
    async def conversation_public_detail(
        input_: dict, _claims: TokenClaims | None
    ) -> dict:
        return await conversations.public_detail(str(input_.get("shareId") or ""))

    @router.procedure("conversation.toggleShare")
    async def toggle_conversation_share(
        input_: dict, claims: TokenClaims | None
    ) -> dict:
        return await conversations.toggle_share(
            policies.current_account_id(claims),
            input_.get("id"),
            bool(input_.get("isShare")),
        )

    @router.procedure("conversation.update")
    async def update_conversation(input_: dict, claims: TokenClaims | None) -> dict:
        return await conversations.update_title(
            policies.current_account_id(claims),
            input_.get("id"),
            input_.get("title"),
        )

    @router.procedure("conversation.clearMessages")
    async def clear_conversation_messages(
        input_: dict, claims: TokenClaims | None
    ) -> dict:
        return await conversations.clear_messages(
            policies.current_account_id(claims), input_.get("id")
        )

    @router.procedure("conversation.delete")
    async def delete_conversation(input_: dict, claims: TokenClaims | None) -> dict:
        return await conversations.delete_conversation(
            policies.current_account_id(claims), input_.get("id")
        )

    # -- message ----------------------------------------------------------

    @router.procedure("message.create")
    async def create_message(input_: dict, claims: TokenClaims | None) -> dict:
        return await messages.create_message(
            policies.current_account_id(claims), input_
        )

    @router.procedure("message.list")
    async def list_messages(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await messages.list_messages(
            policies.current_account_id(claims),
            input_.get("conversationId"),
            _int(input_.get("page"), 1),
            _int(input_.get("size"), 20),
        )

    @router.procedure("message.update")
    async def update_message(input_: dict, claims: TokenClaims | None) -> dict:
        return await messages.update_message(
            policies.current_account_id(claims), input_
        )

    @router.procedure("message.delete")
    async def delete_message(input_: dict, claims: TokenClaims | None) -> dict:
        return await messages.delete_message(
            policies.current_account_id(claims), input_.get("id")
        )

    @router.procedure("message.clearAfter")
    async def clear_after(input_: dict, claims: TokenClaims | None) -> dict:
        return await messages.clear_after(
            policies.current_account_id(claims), input_.get("id")
        )

    # -- follows ----------------------------------------------------------

    @router.procedure("follows.recommandList")
    async def recommand_list(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await follows.recommand_list(
            policies.current_account_id(claims),
            str(input_.get("searchText") or ""),
        )

    @router.procedure("follows.follow")
    async def follow_site(input_: dict, claims: TokenClaims | None) -> dict:
        return await follows.follow(policies.current_account_id(claims), input_)

    @router.procedure("follows.unfollow")
    async def unfollow_site(input_: dict, claims: TokenClaims | None) -> bool:
        return await follows.unfollow(policies.current_account_id(claims), input_)

    @router.procedure("follows.followList")
    async def follow_list(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await follows.follow_list(
            policies.current_account_id(claims), input_.get("userId")
        )

    @router.procedure("follows.followerList")
    async def follower_list(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await follows.follower_list(
            policies.current_account_id(claims), input_.get("userId")
        )

    # -- shareApprovals ---------------------------------------------------

    @router.procedure("shareApprovals.policy")
    async def share_policy(input_: dict, claims: TokenClaims | None) -> dict:
        return await share_approvals.policy(
            policies.current_account_id(claims), _is_admin(claims)
        )

    @router.procedure("shareApprovals.inbox")
    async def share_inbox(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await share_approvals.inbox(
            policies.current_account_id(claims), input_.get("status") or "pending"
        )

    @router.procedure("shareApprovals.outgoing")
    async def share_outgoing(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await share_approvals.outgoing(
            policies.current_account_id(claims),
            input_.get("noteId"),
            input_.get("status"),
        )

    @router.procedure("shareApprovals.pendingAdmin")
    async def share_pending_admin(
        input_: dict, claims: TokenClaims | None
    ) -> list[dict]:
        policies.require_superadmin(claims)
        return await share_approvals.pending_admin(input_.get("status") or "pending")

    @router.procedure("shareApprovals.requestInternal")
    async def share_request_internal(
        input_: dict, claims: TokenClaims | None
    ) -> list[dict]:
        return await share_approvals.request_internal(
            policies.current_account_id(claims), input_
        )

    @router.procedure("shareApprovals.inviteByEmail")
    async def share_invite_by_email(input_: dict, claims: TokenClaims | None) -> dict:
        return await share_approvals.invite_by_email(
            policies.current_account_id(claims), input_
        )

    @router.procedure("shareApprovals.requestPublic")
    async def share_request_public(input_: dict, claims: TokenClaims | None) -> dict:
        return await share_approvals.request_public(
            policies.current_account_id(claims), input_
        )

    @router.procedure("shareApprovals.decide")
    async def share_decide(input_: dict, claims: TokenClaims | None) -> dict:
        return await share_approvals.decide(
            policies.current_account_id(claims), _is_admin(claims), input_
        )

    @router.procedure("shareApprovals.acceptInvite")
    async def share_accept_invite(input_: dict, claims: TokenClaims | None) -> dict:
        return await share_approvals.accept_invite(
            policies.current_account_id(claims), str(input_.get("token") or "")
        )

    @router.procedure("shareApprovals.revoke")
    async def share_revoke(input_: dict, claims: TokenClaims | None) -> dict:
        return await share_approvals.revoke(
            policies.current_account_id(claims), _is_admin(claims), input_.get("id")
        )

    @router.procedure("shareApprovals.setPolicy")
    async def share_set_policy(input_: dict, claims: TokenClaims | None) -> dict:
        policies.require_superadmin(claims)
        return await share_approvals.set_policy(
            bool(input_.get("requireShareApproval"))
        )

    @router.procedure("shareApprovals.forNote")
    async def share_for_note(input_: dict, claims: TokenClaims | None) -> list[dict]:
        return await share_approvals.for_note(
            policies.current_account_id(claims), input_.get("noteId")
        )
