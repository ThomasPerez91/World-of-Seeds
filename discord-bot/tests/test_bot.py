import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
from app import (
    AccessGate,
    MemberBusy,
    RecoveryConfirmation,
    RegistrationBot,
    RegistrationView,
    Settings,
    State,
)
from wos import ApiError, Credentials, Onboarding, UserError, WosApi

GUILD = "1549418373308358727"
CHANNEL = "1549532212402196511"
MEMBER = "123456789012345678"
KEY = "wos_live_FAKE_FOR_OFFLINE_TESTS"
SEED = "A" * 25
PASSWORD = "Fake-Password-123"


def account(**changes):
    return {
        "username": "test-user",
        "is_active": True,
        "deleted": False,
        "must_change_credentials": True,
        **changes,
    }


def credentials(**changes):
    return {
        "username": "test-user",
        "temporary_password": PASSWORD,
        "auth_seed": SEED,
        **changes,
    }


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        if hasattr(self, "api"):
            await self.api.close()

    def build(self, handler):
        self.api = WosApi(
            "https://wos.example",
            KEY,
            transport=httpx.MockTransport(handler),
            interval=0,
        )
        return self.api

    async def test_registration_authenticates_and_sends_only_interaction_identity(self):
        seen = []

        def handle(request):
            seen.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer " + KEY)
            self.assertNotIn(KEY, str(request.url))
            if request.url.path.endswith("/capacity"):
                return httpx.Response(200, json={"reached": False, "available": 3})
            if request.method == "GET":
                return httpx.Response(
                    404, json={"error": {"code": "discord_account_not_found"}}
                )
            self.assertEqual(json.loads(request.content), {"discord_user_id": MEMBER})
            self.assertEqual(
                request.headers["Idempotency-Key"], f"discord-register:{GUILD}:{MEMBER}"
            )
            return httpx.Response(201, json=credentials())

        service = Onboarding(self.build(handle), GUILD)
        result = await service.register(MEMBER)
        self.assertEqual(result.password, PASSWORD)
        self.assertEqual(len(seen), 3)
        self.assertNotIn(PASSWORD, repr(result))
        self.assertNotIn(SEED, repr(result))

    async def test_ambiguous_create_can_be_replayed_with_same_key_after_restart(self):
        keys = []

        def handle(request):
            keys.append(request.headers["Idempotency-Key"])
            if len(keys) == 1:
                raise httpx.ReadTimeout("timeout")
            return httpx.Response(200, json=credentials(temporary_password=None))

        api = self.build(handle)
        with self.assertRaises(UserError):
            await api.create(MEMBER, GUILD)
        with self.assertRaisesRegex(UserError, "seconde fois"):
            await api.create(MEMBER, GUILD)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(keys), 2)  # No unbounded automatic mutation retries.

    async def test_existing_or_revoked_account_never_creates_another(self):
        for changes in (
            {},
            {"is_active": False},
            {"deleted": True, "is_active": False},
        ):
            methods = []

            def handle(request, methods=methods, changes=changes):
                methods.append(request.method)
                return httpx.Response(200, json=account(**changes))

            api = self.build(handle)
            with self.assertRaises(UserError):
                await Onboarding(api, GUILD).register(MEMBER)
            self.assertEqual(methods, ["GET"])
            await api.close()

    async def test_full_capacity_never_creates(self):
        def handle(request):
            self.assertEqual(request.method, "GET")
            if request.url.path.endswith("/capacity"):
                return httpx.Response(200, json={"reached": True, "available": 0})
            return httpx.Response(
                404, json={"error": {"code": "discord_account_not_found"}}
            )

        with self.assertRaisesRegex(UserError, "places"):
            await Onboarding(self.build(handle), GUILD).register(MEMBER)

    async def test_lost_capacity_race_uses_server_quota_error(self):
        def handle(request):
            if request.url.path.endswith("/capacity"):
                return httpx.Response(200, json={"reached": False, "available": 1})
            if request.method == "GET":
                return httpx.Response(
                    404, json={"error": {"code": "discord_account_not_found"}}
                )
            return httpx.Response(
                409, json={"error": {"code": "account_quota_reached"}}
            )

        with self.assertRaisesRegex(ApiError, "places"):
            await Onboarding(self.build(handle), GUILD).register(MEMBER)

    async def test_general_reset_accepts_a_previously_used_account(self):
        methods = []

        def handle(request):
            methods.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json=account(must_change_credentials=False))
            self.assertTrue(request.url.path.endswith("/password-reset"))
            return httpx.Response(200, json={"token": "A" * 43, "expires_in": 900})

        self.assertEqual(
            await Onboarding(self.build(handle), GUILD).reset(MEMBER), "A" * 43
        )
        self.assertEqual(methods, ["GET", "POST"])

    async def test_general_reset_timeout_is_never_retried(self):
        methods = []

        def handle(request):
            methods.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json=account(must_change_credentials=False))
            raise httpx.ReadTimeout("timeout")

        with self.assertRaises(UserError):
            await Onboarding(self.build(handle), GUILD).reset(MEMBER)
        self.assertEqual(methods, ["GET", "POST"])

    async def test_recovery_checks_status_and_does_not_retry_password_rotation(self):
        calls = []

        def handle(request):
            calls.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json=account())
            raise httpx.ReadTimeout("timeout")

        with self.assertRaises(UserError):
            await Onboarding(self.build(handle), GUILD).recover(MEMBER)
        self.assertEqual(calls, ["GET", "POST"])

    async def test_recovery_of_claimed_or_disabled_account_is_refused_locally(self):
        for changes in (
            {"must_change_credentials": False},
            {"is_active": False},
            {"deleted": True},
        ):

            def handle(request, changes=changes):
                self.assertEqual(request.method, "GET")
                return httpx.Response(200, json=account(**changes))

            api = self.build(handle)
            with self.assertRaises(UserError):
                await Onboarding(api, GUILD).recover(MEMBER)
            await api.close()

    async def test_server_first_login_race_is_respected(self):
        def handle(request):
            if request.method == "GET":
                return httpx.Response(200, json=account())
            return httpx.Response(
                409, json={"error": {"code": "initial_credentials_unavailable"}}
            )

        with self.assertRaisesRegex(ApiError, "premier|connecté"):
            await Onboarding(self.build(handle), GUILD).recover(MEMBER)

    async def test_429_blocks_subsequent_calls_and_respects_retry_after(self):
        calls = []

        def handle(request):
            calls.append(1)
            return httpx.Response(
                429,
                json={"error": {"code": "rate_limit_exceeded"}},
                headers={"Retry-After": "60"},
            )

        api = self.build(handle)
        for _ in range(2):
            with self.assertRaises(ApiError) as exc:
                await api.capacity()
            self.assertEqual(exc.exception.status, 429)
        self.assertEqual(len(calls), 1)

    async def test_unknown_errors_never_expose_server_bodies_or_secrets(self):
        def handle(request):
            return httpx.Response(
                500, json={"error": {"code": PASSWORD, "message": KEY}}
            )

        with self.assertRaises(UserError) as exc:
            await self.build(handle).capacity()
        self.assertNotIn(PASSWORD, str(exc.exception))
        self.assertNotIn(KEY, str(exc.exception))

    async def test_redirect_is_not_followed_with_authorization(self):
        calls = []

        def handle(request):
            calls.append(request.url)
            return httpx.Response(302, headers={"Location": "https://other.example"})

        with self.assertRaises(UserError):
            await self.build(handle).capacity()
        self.assertEqual(len(calls), 1)

    async def test_read_response_rejects_wrong_boolean_types(self):
        self.build(lambda request: httpx.Response(200, json=account(is_active="false")))
        with self.assertRaises(UserError):
            await self.api.account(MEMBER)

    async def test_invalid_json_is_a_safe_error(self):
        with self.assertRaises(UserError):
            await self.build(
                lambda request: httpx.Response(200, text="SECRET")
            ).capacity()

    async def test_invalid_origin_is_rejected(self):
        for url in (
            "http://wos.example",
            "https://user:pass@wos.example",
            "https://wos.example/?key=secret",
            "https://wos.example/api",
        ):
            with self.assertRaises(ValueError):
                WosApi(url, KEY)


class StateTests(unittest.TestCase):
    def test_panel_survives_restart_and_single_process_is_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            state = State(path)
            state.save_panel(GUILD, CHANNEL, 123)
            with self.assertRaises(ValueError):
                State(path)
            state.close()
            second = State(path)
            self.assertEqual(second.message_id(GUILD, CHANNEL), 123)
            self.assertIsNone(second.message_id(GUILD, "223456789012345678"))
            second.close()

    def test_member_gate_prevents_concurrent_mutations(self):
        gate = AccessGate()
        gate.enter(1)
        with self.assertRaises(MemberBusy):
            gate.enter(1)
        gate.leave(1)
        with self.assertRaises(UserError):
            gate.enter(1)

    def test_secrets_not_in_settings_repr(self):
        settings = Settings(GUILD, CHANNEL, "https://wos.example", "fake-token", KEY)
        self.assertNotIn("fake-token", repr(settings))
        self.assertNotIn(KEY, repr(settings))


class DiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.state = State(Path(self.directory.name))
        settings = Settings(
            GUILD,
            CHANNEL,
            "https://wos.example",
            "fake-token",
            KEY,
            Path(self.directory.name),
        )
        self.bot = RegistrationBot(settings, self.state)

    async def asyncTearDown(self):
        await self.bot.close()
        self.state.close()
        self.directory.cleanup()

    def interaction(self, **changes):
        return SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(),
            user=SimpleNamespace(id=int(MEMBER), bot=False, send=AsyncMock()),
            guild_id=changes.get("guild", int(GUILD)),
            channel_id=changes.get("channel", int(CHANNEL)),
        )

    async def test_persistent_buttons_survive_and_foreign_guild_is_refused(self):
        view = RegistrationView(self.bot)
        self.assertTrue(view.is_persistent())
        self.assertEqual(len({button.custom_id for button in view.children}), 5)
        for interaction in (self.interaction(guild=123), self.interaction(channel=123)):
            self.assertFalse(await view.interaction_check(interaction))
            self.assertTrue(
                interaction.response.send_message.call_args.kwargs["ephemeral"]
            )
        self.assertTrue(await view.interaction_check(self.interaction()))
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.message_content)
        self.assertFalse(self.bot.intents.presences)

    async def test_credentials_sent_only_to_invoking_member(self):
        interaction = self.interaction()
        self.bot.service.register = AsyncMock(
            return_value=Credentials("test-user", PASSWORD, SEED)
        )
        await self.bot.execute(interaction, "register")
        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True, thinking=True
        )
        self.bot.service.register.assert_awaited_once_with(MEMBER)
        self.assertIn(PASSWORD, interaction.user.send.call_args.args[0])
        self.assertNotIn(PASSWORD, str(interaction.edit_original_response.call_args))

    async def test_blocked_dm_falls_back_to_ephemeral_without_rotating_password(self):
        interaction = self.interaction()
        interaction.user.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "blocked"
        )
        self.bot.service.register = AsyncMock(
            return_value=Credentials("test-user", PASSWORD, SEED)
        )
        self.bot.service.recover = AsyncMock()
        await self.bot.execute(interaction, "register")
        self.assertIn(
            PASSWORD, interaction.edit_original_response.call_args.kwargs["content"]
        )
        self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
        self.bot.service.recover.assert_not_awaited()

    async def test_reset_link_is_private_and_reused_if_dm_delivery_fails(self):
        interaction = self.interaction()
        interaction.user.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "blocked"
        )
        self.bot.service.reset = AsyncMock(return_value="A" * 43)
        await self.bot.execute(interaction, "reset")
        self.bot.service.reset.assert_awaited_once_with(MEMBER)
        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True, thinking=True
        )
        self.assertIn(
            "https://wos.example/#reset-password=" + "A" * 43,
            interaction.edit_original_response.call_args.kwargs["content"],
        )

    async def test_registration_role_is_required_only_when_configured(self):
        self.bot.settings = replace(
            self.bot.settings, registration_role="999999999999999999"
        )
        self.bot.service.register = AsyncMock(
            return_value=Credentials("test-user", PASSWORD, SEED)
        )
        interaction = self.interaction()
        interaction.user.roles = []
        await self.bot.execute(interaction, "register")
        self.bot.service.register.assert_not_awaited()
        self.assertIn(
            "validée", interaction.edit_original_response.call_args.kwargs["content"]
        )
        interaction.user.roles = [SimpleNamespace(id=999999999999999999)]
        await self.bot.execute(interaction, "register")
        self.bot.service.register.assert_awaited_once_with(MEMBER)

    async def test_second_concurrent_request_is_rejected(self):
        interaction = self.interaction()
        self.bot.gate.enter(interaction.user.id)
        self.bot.service.register = AsyncMock()
        await self.bot.execute(interaction, "register")
        self.bot.service.register.assert_not_awaited()
        self.assertIn(
            "déjà en cours",
            interaction.edit_original_response.call_args.kwargs["content"],
        )
        self.bot.gate.leave(interaction.user.id)

    async def test_recovery_confirmation_is_bound_to_member_and_channel(self):
        view = RecoveryConfirmation(self.bot, int(MEMBER))
        other = self.interaction()
        other.user.id = 223456789012345678
        self.assertFalse(await view.interaction_check(other))
        self.assertFalse(await view.interaction_check(self.interaction(channel=1)))
        view.used = True
        self.assertFalse(await view.interaction_check(self.interaction()))

    async def test_restart_edits_saved_panel_without_reading_history(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.guild = SimpleNamespace(id=int(GUILD), me=object())
        channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True, send_messages=True, embed_links=True
        )
        channel.send = AsyncMock(return_value=SimpleNamespace(id=123))
        partial = SimpleNamespace(edit=AsyncMock())
        channel.get_partial_message.return_value = partial
        self.bot.fetch_channel = AsyncMock(return_value=channel)
        self.bot.panel = RegistrationView(self.bot)
        await self.bot.on_ready()
        await self.bot.on_ready()
        channel.send.assert_awaited_once()
        partial.edit.assert_awaited_once()
        channel.history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
