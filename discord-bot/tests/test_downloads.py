import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from app import DownloadsView, RegistrationBot, Settings, State
from downloads import DeliveryState, download_embed
from test_bot import CHANNEL, GUILD, KEY, MEMBER
from wos import WosApi


def notice(**changes):
    now = datetime.now(UTC)
    return {
        "request_id": "00000000-0000-0000-0000-000000000001",
        "discord_user_id": MEMBER,
        "name": "Film @everyone ** secret",
        "ready_at": (now - timedelta(hours=1)).isoformat(),
        "unsubscribe_at": (now + timedelta(hours=8)).isoformat(),
        "warning_at": (now + timedelta(hours=5)).isoformat(),
        **changes,
    }


class StoreTests(unittest.TestCase):
    def test_restart_dedup_preferences_and_resubscription(self):
        with tempfile.TemporaryDirectory() as directory:
            item = notice()
            state = DeliveryState(Path(directory))
            self.assertTrue(state.claim(item, "ready"))
            self.assertFalse(state.claim(item, "ready"))
            self.assertFalse(state.toggle(MEMBER))
            state.advance(item["request_id"])
            state.close()
            state = DeliveryState(Path(directory))
            self.assertFalse(state.claim(item, "ready"))
            self.assertFalse(state.enabled(MEMBER))
            self.assertEqual(state.cursor(), item["request_id"])
            self.assertTrue(
                state.claim(
                    {**item, "ready_at": datetime.now(UTC).isoformat()}, "ready"
                )
            )
            self.assertTrue(state.claim(item, "warning"))
            self.assertFalse(state.claim(item, "warning"))
            state.close()

    def test_embed_bounds_untrusted_names_and_red_warning(self):
        page = {
            "items": [
                {
                    "name": "*" * 4096,
                    "state": "ready",
                    "unsubscribe_at": notice()["unsubscribe_at"],
                    "access_expiring_soon": True,
                }
            ],
            "offset": 0,
            "limit": 8,
            "total": 1,
        }
        embed = download_embed(page, "https://wos.example")
        self.assertEqual(embed.color, discord.Color.red())
        self.assertLessEqual(len(embed.fields[0].name), 256)
        self.assertIn("🔴", embed.fields[0].value)


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name)
        self.state = State(path)
        self.bot = RegistrationBot(
            Settings(GUILD, CHANNEL, "https://wos.example", "fake-token", KEY, path),
            self.state,
        )
        self.user = SimpleNamespace(send=AsyncMock())
        self.bot.fetch_user = AsyncMock(return_value=self.user)
        self.item = notice()
        self.bot.api.notices = AsyncMock(
            return_value={"items": [self.item], "next_after": None}
        )

    async def asyncTearDown(self):
        await self.bot.close()
        self.state.close()
        self.directory.cleanup()

    async def test_ready_then_warning_once_to_correct_member(self):
        await self.bot.notification_cycle()
        await self.bot.notification_cycle()
        self.user.send.assert_awaited_once()
        self.bot.fetch_user.assert_awaited_once_with(int(MEMBER))
        self.assertEqual(self.user.send.call_args.kwargs["embed"].color.value, 0x34D399)
        self.item["warning_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await self.bot.notification_cycle()
        await self.bot.notification_cycle()
        self.assertEqual(self.user.send.await_count, 2)
        self.assertEqual(
            self.user.send.call_args.kwargs["embed"].color, discord.Color.red()
        )
        self.assertFalse(self.user.send.call_args.kwargs["allowed_mentions"].everyone)

    async def test_downtime_sends_only_red_warning_not_two_messages(self):
        self.item["warning_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await self.bot.notification_cycle()
        await self.bot.notification_cycle()
        self.user.send.assert_awaited_once()
        self.assertEqual(
            self.user.send.call_args.kwargs["embed"].color, discord.Color.red()
        )

    async def test_disabled_expired_cancelled_and_suspended_never_send(self):
        self.bot.deliveries.toggle(MEMBER)
        await self.bot.notification_cycle()
        self.bot.deliveries.toggle(MEMBER)
        self.item["unsubscribe_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        await self.bot.notification_cycle()
        # API removes cancelled/suspended subscriptions from its feed.
        self.bot.api.notices.return_value = {"items": [], "next_after": None}
        await self.bot.notification_cycle()
        self.user.send.assert_not_awaited()

    async def test_blocked_dm_has_no_public_fallback_or_repeat_spam(self):
        self.user.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "PRIVATE-TITLE"
        )
        with self.assertLogs("wos_discord", level="WARNING") as output:
            await self.bot.notification_cycle()
        await self.bot.notification_cycle()
        self.user.send.assert_awaited_once()
        self.assertNotIn("PRIVATE-TITLE", str(output.output))
        self.assertNotIn(MEMBER, str(output.output))

    async def test_private_list_and_navigation_revalidate_interaction_author(self):
        page = {"items": [], "offset": 0, "limit": 8, "total": 9}
        self.bot.api.downloads = AsyncMock(return_value=page)
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
            user=SimpleNamespace(id=int(MEMBER)),
            guild_id=int(GUILD),
            channel_id=int(CHANNEL),
        )
        await self.bot.execute(interaction, "downloads")
        interaction.response.defer.assert_awaited_once_with(
            ephemeral=True, thinking=True
        )
        self.bot.api.downloads.assert_awaited_once_with(MEMBER)
        view = interaction.edit_original_response.call_args.kwargs["view"]
        self.assertIsInstance(view, DownloadsView)
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)
        interaction.user.id += 1
        self.assertFalse(await view.interaction_check(interaction))

    async def test_pagination_cursor_survives_cycle(self):
        self.bot.api.notices.return_value = {
            "items": [],
            "next_after": self.item["request_id"],
        }
        await self.bot.notification_cycle()
        await self.bot.notification_cycle()
        self.bot.api.notices.assert_awaited_with(self.item["request_id"])

    async def test_api_uses_explicit_discord_route_without_seed(self):
        import httpx

        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(
                200, json={"items": [], "offset": 0, "limit": 8, "total": 0}
            )

        api = WosApi(
            "https://wos.example",
            KEY,
            transport=httpx.MockTransport(handler),
            interval=0,
        )
        try:
            await api.downloads(MEMBER)
        finally:
            await api.close()
        self.assertEqual(
            seen[0].url.path, f"/api/external/v1/users/discord/{MEMBER}/downloads"
        )
        self.assertNotIn("X-WOS-User-Seed", seen[0].headers)
