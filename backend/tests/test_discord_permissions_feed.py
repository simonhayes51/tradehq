import asyncio

from app.services.discord_permissions import check_bot_channel_access


def run(coro):
    return asyncio.run(coro)


class FakeDiscord:
    def __init__(self, *, channel=(200, {"type": 0, "permission_overwrites": []}), guild=(200, {}), bot=(200, {"id": "bot"}), roles=None, member=None, post=(201, {"id": "m1"}), delete=(204, "")):
        self.channel = channel
        self.guild = guild
        self.bot = bot
        self.roles = roles if roles is not None else (200, [{"id": "g1", "permissions": str((1 << 10) | (1 << 11) | (1 << 14))}])
        self.member = member if member is not None else (200, {"roles": []})
        self.post = post
        self.delete = delete
        self.post_calls = 0
        self.delete_calls = 0

    async def get_channel(self, channel_id):
        return self.channel

    async def get_guild(self, guild_id):
        return self.guild

    async def get_bot_user(self):
        return self.bot

    async def get_guild_roles(self, guild_id):
        return self.roles

    async def get_guild_member_in(self, guild_id, user_id):
        return self.member

    async def post_embed(self, channel_id, embed, *, nonce, enforce_nonce, suppress_notifications=False, components=None):
        self.post_calls += 1
        return self.post

    async def delete_message(self, channel_id, message_id):
        self.delete_calls += 1
        return self.delete


def test_channel_403_with_accessible_guild_is_missing_permissions():
    ok, reason = run(check_bot_channel_access(FakeDiscord(channel=(403, {}), guild=(200, {})), "g1", "c1"))
    assert ok is False
    assert reason == "missing_permissions"


def test_channel_403_with_inaccessible_guild_is_bot_not_in_guild():
    ok, reason = run(check_bot_channel_access(FakeDiscord(channel=(403, {}), guild=(403, {})), "g1", "c1"))
    assert ok is False
    assert reason == "bot_not_in_guild"


def test_discord_401_is_reported_as_bot_auth_failure():
    ok, reason = run(check_bot_channel_access(FakeDiscord(channel=(401, {})), "g1", "c1"))
    assert ok is False
    assert reason == "bot_auth_failed"


def test_network_failure_remains_discord_unavailable():
    ok, reason = run(check_bot_channel_access(FakeDiscord(channel=(0, {"message": "timeout"})), "g1", "c1"))
    assert ok is False
    assert reason == "discord_unavailable"


def test_valid_channel_and_permissions_still_pass():
    ok, reason = run(check_bot_channel_access(FakeDiscord(), "g1", "c1"))
    assert ok is True
    assert reason is None


def test_false_negative_permission_math_is_overridden_by_real_post_probe():
    # Base role has only View Channel. The local permission calculation says
    # the bot cannot send/embed, but Discord accepts a real probe, so setup
    # must trust Discord instead of rejecting the channel.
    fake = FakeDiscord(
        roles=(200, [{"id": "g1", "permissions": str(1 << 10)}]),
        post=(201, {"id": "probe1"}),
        delete=(204, ""),
    )
    ok, reason = run(check_bot_channel_access(fake, "g1", "c1"))
    assert ok is True
    assert reason is None
    assert fake.post_calls == 1
    assert fake.delete_calls == 1


def test_real_probe_403_confirms_missing_permissions():
    fake = FakeDiscord(
        roles=(200, [{"id": "g1", "permissions": str(1 << 10)}]),
        post=(403, {"message": "Missing Permissions"}),
    )
    ok, reason = run(check_bot_channel_access(fake, "g1", "c1"))
    assert ok is False
    assert reason == "missing_permissions"
    assert fake.post_calls == 1
