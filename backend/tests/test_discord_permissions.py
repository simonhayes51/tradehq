from app.services.discord_permissions import (
    ADMINISTRATOR,
    EMBED_LINKS,
    MANAGE_GUILD,
    SEND_MESSAGES,
    VIEW_CHANNEL,
    bot_can_post_embeds,
    has_manage_guild,
    is_dm_interaction,
    resolve_effective_permissions,
)


def test_has_manage_guild_true_when_bit_set():
    payload = {"member": {"permissions": str(MANAGE_GUILD)}}
    assert has_manage_guild(payload) is True


def test_has_manage_guild_true_for_administrator():
    payload = {"member": {"permissions": str(ADMINISTRATOR)}}
    assert has_manage_guild(payload) is True


def test_has_manage_guild_false_without_the_bit():
    payload = {"member": {"permissions": str(SEND_MESSAGES)}}
    assert has_manage_guild(payload) is False


def test_has_manage_guild_false_with_no_member_block_at_all():
    """A DM interaction has `user`, never `member` - never trust an
    absent permissions field as an implicit grant."""
    payload = {"user": {"id": "123"}}
    assert has_manage_guild(payload) is False


def test_has_manage_guild_ignores_any_client_supplied_override():
    """Only interaction_payload['member']['permissions'] - Discord's own
    computed value - is trusted. A spoofed top-level 'permissions' or
    'is_admin' field elsewhere in the payload must never be read."""
    payload = {"member": {"permissions": "0"}, "is_admin": True, "permissions": str(ADMINISTRATOR)}
    assert has_manage_guild(payload) is False


def test_is_dm_interaction_true_without_guild_id():
    assert is_dm_interaction({"user": {"id": "1"}}) is True


def test_is_dm_interaction_false_with_guild_id():
    assert is_dm_interaction({"guild_id": "999", "member": {}}) is False


def test_bot_can_post_embeds_requires_all_three_permissions():
    assert bot_can_post_embeds(VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS) is True
    assert bot_can_post_embeds(VIEW_CHANNEL | SEND_MESSAGES) is False
    assert bot_can_post_embeds(0) is False


def test_effective_permissions_administrator_bypasses_overwrites():
    perms = resolve_effective_permissions(
        guild_id="1", everyone_role_permissions=ADMINISTRATOR,
        member_role_ids=[], member_roles_permissions={}, member_user_id="bot",
        channel_overwrites=[{"id": "1", "type": 0, "deny": str(VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS), "allow": "0"}],
    )
    assert bot_can_post_embeds(perms) is True


def test_effective_permissions_everyone_deny_overwrite_removes_access():
    perms = resolve_effective_permissions(
        guild_id="guild1",
        everyone_role_permissions=VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS,
        member_role_ids=[], member_roles_permissions={}, member_user_id="bot",
        channel_overwrites=[{"id": "guild1", "type": 0, "deny": str(SEND_MESSAGES), "allow": "0"}],
    )
    assert bot_can_post_embeds(perms) is False


def test_effective_permissions_role_allow_overwrite_grants_access():
    perms = resolve_effective_permissions(
        guild_id="guild1",
        everyone_role_permissions=VIEW_CHANNEL,
        member_role_ids=["role1"], member_roles_permissions={"role1": 0}, member_user_id="bot",
        channel_overwrites=[
            {"id": "guild1", "type": 0, "deny": "0", "allow": "0"},
            {"id": "role1", "type": 0, "deny": "0", "allow": str(SEND_MESSAGES | EMBED_LINKS)},
        ],
    )
    assert bot_can_post_embeds(perms) is True


def test_effective_permissions_member_overwrite_takes_final_precedence():
    perms = resolve_effective_permissions(
        guild_id="guild1",
        everyone_role_permissions=VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS,
        member_role_ids=["role1"], member_roles_permissions={"role1": 0}, member_user_id="bot",
        channel_overwrites=[
            {"id": "role1", "type": 0, "deny": str(SEND_MESSAGES), "allow": "0"},
            {"id": "bot", "type": 1, "deny": "0", "allow": str(SEND_MESSAGES)},
        ],
    )
    assert bot_can_post_embeds(perms) is True
