"""Discord registration bot. Only configured guild/channel interactions are accepted."""

import argparse
import asyncio
import fcntl
import json
import logging
import os
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import discord
from wos import Credentials, Onboarding, UserError, WosApi, discord_id

BOT_VERSION = "2.3.28"
LOG = logging.getLogger("wos_discord")


@dataclass(frozen=True)
class Settings:
    guild: str
    channel: str
    url: str
    token: str = field(repr=False)
    key: str = field(repr=False)
    data: Path = Path("/app/data")
    registration_role: str | None = None

    @classmethod
    def load(cls):
        values = {}
        for name in (
            "DISCORD_GUILD_ID",
            "DISCORD_CHANNEL_ID",
            "WOS_BASE_URL",
            "DISCORD_BOT_TOKEN",
            "WOS_API_KEY",
        ):
            value = os.environ.get(name, "").strip()
            if not value or "REMPLACER" in value:
                raise ValueError(f"La variable {name} doit être renseignée.")
            values[name] = value
        token = values["DISCORD_BOT_TOKEN"]
        if token.startswith("Bot ") or any(c.isspace() for c in token):
            raise ValueError("DISCORD_BOT_TOKEN doit contenir le token seul.")
        return cls(
            discord_id(values["DISCORD_GUILD_ID"]),
            discord_id(values["DISCORD_CHANNEL_ID"]),
            values["WOS_BASE_URL"],
            token,
            values["WOS_API_KEY"],
            Path(os.environ.get("BOT_DATA_DIR", "/app/data")),
            discord_id(os.environ["DISCORD_REGISTRATION_ROLE_ID"])
            if os.environ.get("DISCORD_REGISTRATION_ROLE_ID")
            else None,
        )


class State:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.lockfile = (directory / "process.lock").open("a")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            raise ValueError(
                "Un autre bot utilise déjà ce volume de données."
            ) from None

    def message_id(self, guild: str, channel: str) -> int | None:
        path = self.directory / "panel.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if data["guild"] == guild and data["channel"] == channel:
                return int(data["message"])
            return None
        except (ValueError, KeyError, TypeError):
            raise ValueError(
                "panel.json est invalide ; vérifier ce fichier avant de republier."
            ) from None

    def save_panel(self, guild: str, channel: str, message: int):
        temporary = self.directory / "panel.tmp"
        temporary.write_text(
            json.dumps({"guild": guild, "channel": channel, "message": str(message)})
        )
        temporary.replace(self.directory / "panel.json")

    def close(self):
        self.lockfile.close()


class MemberBusy(UserError):
    def __init__(self):
        super().__init__(
            "Une opération est déjà en cours pour ton compte. Attends sa réponse."
        )


class AccessGate:
    def __init__(self):
        self.active: set[int] = set()
        self.cooldown: dict[int, float] = {}

    def enter(self, member: int):
        now = time.monotonic()
        if member in self.active:
            raise MemberBusy()
        # Bound retained IDs; expired entries do not accumulate across the bot's lifetime.
        self.cooldown = {
            key: until for key, until in self.cooldown.items() if until > now
        }
        if member in self.cooldown:
            raise UserError("Patiente quelques secondes avant une autre demande.")
        if len(self.active) >= 20:
            raise UserError(
                "Le bot traite déjà plusieurs demandes. Réessaie dans une minute."
            )
        self.active.add(member)

    def leave(self, member: int):
        self.active.discard(member)
        self.cooldown[member] = time.monotonic() + 10


class RegistrationView(discord.ui.View):
    def __init__(self, bot: "RegistrationBot"):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction):
        if (
            interaction.guild_id != int(self.bot.settings.guild)
            or interaction.channel_id != int(self.bot.settings.channel)
            or interaction.user.bot
        ):
            await interaction.response.send_message(
                "Ce panneau est réservé au salon d’inscription WoS.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Créer mon compte WoS",
        style=discord.ButtonStyle.success,
        custom_id="wos:register:v1",
    )
    async def register(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self.bot.execute(interaction, "register")

    @discord.ui.button(
        label="Mon compte",
        style=discord.ButtonStyle.secondary,
        custom_id="wos:status:v1",
    )
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.execute(interaction, "status")

    @discord.ui.button(
        label="Réinitialiser mon mot de passe",
        style=discord.ButtonStyle.secondary,
        custom_id="wos:reset:v1",
    )
    async def recover(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_message(
            "Recevoir un lien privé valable 15 minutes pour choisir un nouveau mot de passe sur WoS ? "
            "Ton mot de passe actuel reste valable jusqu’à validation sur le site.",
            ephemeral=True,
            view=RecoveryConfirmation(self.bot, interaction.user.id),
        )

    async def on_error(self, interaction, error, item):
        # Never log exception strings/tracebacks that may contain HTTP bodies or credentials.
        LOG.error("Interaction interrompue (%s)", type(error).__name__)


class RecoveryConfirmation(discord.ui.View):
    def __init__(self, bot: "RegistrationBot", member: int):
        super().__init__(timeout=120)
        self.bot, self.member = bot, member
        self.used = False

    async def interaction_check(self, interaction: discord.Interaction):
        if (
            interaction.user.id != self.member
            or interaction.guild_id != int(self.bot.settings.guild)
            or interaction.channel_id != int(self.bot.settings.channel)
            or self.used
        ):
            await interaction.response.send_message(
                "Confirmation indisponible.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.used = True
        self.stop()
        await self.bot.execute(interaction, "reset")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.used = True
        self.stop()
        await interaction.response.edit_message(
            content="Récupération annulée.", view=None
        )

    async def on_error(self, interaction, error, item):
        LOG.error("Confirmation interrompue (%s)", type(error).__name__)


def credentials_message(credentials: Credentials, url: str) -> str:
    return (
        f"**Tes accès World of Seeds**\n\nSite : {url}\n"
        f"Identifiant : `{credentials.username}`\n"
        f"Mot de passe temporaire : `{credentials.password}`\n"
        f"Seed d’authentification : `{credentials.seed}`\n"
        "Clé personnelle pour les intégrations API : inutile pour te connecter au site. "
        "Ce n’est pas un code de récupération. Garde-la confidentielle.\n\n"
        "Connecte-toi puis modifie tes identifiants comme demandé par WoS. "
        "Conserve ces accès dans ton gestionnaire de mots de passe."
    )


class RegistrationBot(discord.Client):
    def __init__(self, settings: Settings, state: State):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(
            intents=intents, allowed_mentions=discord.AllowedMentions.none()
        )
        self.settings, self.state = settings, state
        self.api = WosApi(settings.url, settings.key)
        self.service = Onboarding(self.api, settings.guild)
        self.gate = AccessGate()
        self.panel_lock = asyncio.Lock()
        self.heartbeat_task = None

    async def setup_hook(self):
        await self.api.capacity()  # Fail early on a revoked key or missing users:read.
        self.panel = RegistrationView(self)
        self.add_view(self.panel)
        self.heartbeat_task = asyncio.create_task(self.heartbeat())

    async def heartbeat(self):
        while True:
            if self.is_ready():
                (self.settings.data / "heartbeat").write_text(str(time.time()))
            await asyncio.sleep(20)

    async def on_error(self, event_method, *args, **kwargs):
        LOG.error("Événement Discord interrompu : %s", event_method)

    async def on_ready(self):
        async with self.panel_lock:
            try:
                channel = await self.fetch_channel(int(self.settings.channel))
                if not isinstance(
                    channel, discord.TextChannel
                ) or channel.guild.id != int(self.settings.guild):
                    LOG.error(
                        "Le salon configuré doit être un salon textuel du serveur configuré."
                    )
                    await self.close()
                    return
                permissions = channel.permissions_for(channel.guild.me)
                if not (
                    permissions.view_channel
                    and permissions.send_messages
                    and permissions.embed_links
                ):
                    LOG.error(
                        "Permissions manquantes dans le salon : voir, envoyer et intégrer des liens."
                    )
                    await self.close()
                    return
                embed = discord.Embed(
                    title="Bienvenue sur World of Seeds",
                    color=0x34D399,
                    description=(
                        "Après avoir lu les informations et les règles du serveur, "
                        "utilise le bouton ci-dessous pour demander ton compte gratuit.\n\n"
                        "WoS génère tes accès si une place est disponible. "
                        "Tes identifiants te sont transmis en privé.\n\n"
                        "Un seul compte par utilisateur Discord. "
                        "Un compte suspendu ou supprimé ne peut pas être recréé ici."
                        + (
                            "\n\nUne validation administrative est nécessaire avant la création du compte."
                            if self.settings.registration_role is not None
                            else ""
                        )
                    ),
                )
                message_id = self.state.message_id(
                    self.settings.guild, self.settings.channel
                )
                if message_id is not None:
                    try:
                        # Editing our own known message needs no history-reading permission.
                        await channel.get_partial_message(message_id).edit(
                            embed=embed, view=self.panel
                        )
                    except discord.NotFound:
                        message_id = None
                if message_id is None:
                    message = await channel.send(embed=embed, view=self.panel)
                    self.state.save_panel(
                        self.settings.guild, self.settings.channel, message.id
                    )
                LOG.info(
                    "Bot prêt (%s) ; panneau d’inscription disponible dans le salon configuré.",
                    BOT_VERSION,
                )
            except (discord.HTTPException, OSError, ValueError) as exc:
                LOG.error(
                    "Installation du panneau interrompue (%s). Vérifier les permissions et le volume.",
                    type(exc).__name__,
                )
                await self.close()

    async def execute(self, interaction: discord.Interaction, action: str):
        # Acknowledge before waiting for WoS or the global request pacing.
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user.id
        entered = False
        try:
            if action == "register" and self.settings.registration_role is not None:
                roles = getattr(interaction.user, "roles", ())
                if not any(
                    str(role.id) == self.settings.registration_role for role in roles
                ):
                    raise UserError(
                        "Ton inscription doit être validée par un administrateur Discord."
                    )
            self.gate.enter(member)
            entered = True
            if action == "status":
                await interaction.edit_original_response(
                    content=await self.service.status(str(member))
                )
                return
            if action == "reset":
                token = await self.service.reset(str(member))
                content = (
                    "**Réinitialiser ton mot de passe WoS**\n\n"
                    f"{self.settings.url.rstrip('/')}/#reset-password={token}\n\n"
                    "Lien personnel valable 15 minutes et utilisable une seule fois. "
                    "Ouvre-le pour choisir ton nouveau mot de passe. Ne le partage pas. "
                    "Si tu demandes un nouveau lien, utilise uniquement le dernier reçu."
                )
            else:
                credentials = await self.service.register(str(member))
                content = credentials_message(credentials, self.settings.url)
            try:
                await interaction.user.send(content, suppress_embeds=True)
            except discord.HTTPException:
                # The deferred response is always ephemeral. Reuse identical credentials,
                # including after an ambiguous DM failure; never rotate them during delivery.
                await interaction.edit_original_response(content=content)
            else:
                await interaction.edit_original_response(
                    content="Le message WoS a été envoyé en privé."
                )
        except UserError as exc:
            await interaction.edit_original_response(content=str(exc))
        except Exception as exc:  # noqa: BLE001 - sanitize unexpected errors at the interaction boundary.
            LOG.error("Opération interrompue (%s)", type(exc).__name__)
            with suppress(discord.HTTPException):
                await interaction.edit_original_response(
                    content="L’opération n’a pas pu être confirmée. Utilise « Mon compte » pour vérifier son résultat."
                )
        finally:
            if entered:
                self.gate.leave(member)

    async def close(self):
        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat_task
            self.heartbeat_task = None
        with suppress(OSError):
            (self.settings.data / "heartbeat").unlink(missing_ok=True)
        await self.api.close()
        await super().close()


async def run(settings: Settings, check: bool):
    if check:
        api = WosApi(settings.url, settings.key)
        try:
            capacity = await api.capacity()
            print(
                f"API WoS accessible : {capacity['available']} place(s) disponible(s). Aucun compte créé."
            )
        finally:
            await api.close()
        return
    state = State(settings.data)
    try:
        async with RegistrationBot(settings, state) as bot:
            await bot.start(settings.token)
    finally:
        state.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        try:
            heartbeat = Path(os.environ.get("BOT_DATA_DIR", "/app/data")) / "heartbeat"
            return 0 if time.time() - float(heartbeat.read_text()) < 90 else 1
        except (OSError, ValueError):
            return 1
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    logging.getLogger("discord").setLevel(logging.WARNING)
    try:
        asyncio.run(run(Settings.load(), args.check))
    except (ValueError, UserError) as exc:
        LOG.error("%s", exc)
        return 1
    except discord.LoginFailure:
        LOG.error("Token Discord invalide. Vérifier DISCORD_BOT_TOKEN.")
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - never log secret-bearing transport exceptions.
        LOG.error(
            "Démarrage interrompu (%s). Vérifier la configuration et la connexion.",
            type(exc).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
