"""WoS onboarding API. Credentials never appear in exception messages or reprs."""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


class UserError(Exception):
    pass


class ApiError(UserError):
    def __init__(self, status: int, code: str, retry_after: int = 0):
        self.status, self.code = status, code
        messages = {
            "invalid_api_key": "La clé API WoS est invalide. Contacte l’administrateur.",
            "api_client_disabled": "L’accès API du bot a été révoqué. Contacte l’administrateur.",
            "insufficient_scope": "Une permission manque à la clé du bot. Contacte l’administrateur.",
            "account_quota_reached": "Toutes les places WoS sont actuellement occupées.",
            "discord_account_exists": "Un compte existe déjà. Utilise « Mon compte ».",
            "initial_credentials_unavailable": (
                "La récupération initiale est indisponible. Si tu t’es déjà connecté, "
                "elle ne peut plus remplacer ton mot de passe. Contacte l’administrateur."
            ),
            "user_disabled": "Ton compte WoS est suspendu. Contacte l’administrateur.",
            "password_reset_unavailable": "La récupération est indisponible pour ce compte. Contacte l’administrateur.",
            "discord_account_not_found": "Aucun compte récupérable par ce bot. Contacte l’administrateur.",
        }
        if status == 429:
            message = f"Trop de demandes. Réessaie dans {retry_after or 60} secondes."
        else:
            message = messages.get(
                code, "WoS est momentanément indisponible. Réessaie plus tard."
            )
        super().__init__(message)


def discord_id(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{16,19}", value):
        raise ValueError("Un identifiant Discord doit contenir 17 à 20 chiffres.")
    return value


@dataclass(frozen=True)
class Account:
    username: str
    active: bool
    deleted: bool
    must_change: bool


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str = field(repr=False)
    seed: str = field(repr=False)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "Credentials":
        name, password, seed = (
            data.get(k) for k in ("username", "temporary_password", "auth_seed")
        )
        if (
            not isinstance(name, str)
            or not 3 <= len(name) <= 32
            or not isinstance(password, str)
            or not 1 <= len(password) <= 128
            or not isinstance(seed, str)
            or not re.fullmatch(r"[A-Za-z0-9]{25}", seed)
        ):
            raise UserError("Réponse d’accès inattendue. Contacte l’administrateur.")
        return cls(name, password, seed)


class WosApi:
    def __init__(
        self, base_url: str, key: str, *, transport=None, interval: float = 1.1
    ):
        parts = urlsplit(base_url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in ("", "/")
        ):
            raise ValueError(
                "WOS_BASE_URL doit être une origine HTTPS, sans chemin ni identifiants."
            )
        if (
            not key.startswith("wos_live_")
            or "REMPLACER" in key
            or any(c.isspace() for c in key)
        ):
            raise ValueError("WOS_API_KEY est manquante ou invalide.")
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.base_url + "/api/external/v1/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )
        self.lock = asyncio.Lock()
        self.interval, self.next_at, self.blocked_until = interval, 0.0, 0.0

    async def close(self):
        await self.client.aclose()

    async def request(
        self, method: str, path: str, *, payload=None, key=None
    ) -> dict[str, Any]:
        async with self.lock:
            now = time.monotonic()
            if now < self.blocked_until:
                raise ApiError(
                    429, "rate_limit_exceeded", int(self.blocked_until - now) + 1
                )
            await asyncio.sleep(max(0, self.next_at - now))
            self.next_at = time.monotonic() + self.interval
            headers = {"Idempotency-Key": key} if key else {}
            try:
                response = await self.client.request(
                    method, path, json=payload, headers=headers
                )
            except httpx.TransportError:
                raise UserError(
                    "La réponse WoS n’a pas été reçue. Une création a peut-être abouti : "
                    "utilise « Mon compte » avant de demander une récupération."
                ) from None
            if response.is_error or response.is_redirect:
                try:
                    code = response.json().get("error", {}).get("code", "unknown")
                except (ValueError, AttributeError):
                    code = "unknown"
                # Only retain known protocol codes, never arbitrary response bodies.
                allowed = {
                    "invalid_api_key",
                    "api_client_disabled",
                    "insufficient_scope",
                    "account_quota_reached",
                    "discord_account_exists",
                    "initial_credentials_unavailable",
                    "password_reset_unavailable",
                    "discord_account_not_found",
                    "rate_limit_exceeded",
                    "user_disabled",
                }
                code = code if isinstance(code, str) and code in allowed else "unknown"
                try:
                    delay = max(
                        1, min(300, int(response.headers.get("Retry-After", "60")))
                    )
                except ValueError:
                    delay = 60
                if response.status_code == 429:
                    self.blocked_until = time.monotonic() + delay
                raise ApiError(response.status_code, code, delay)
            try:
                data = response.json()
            except ValueError:
                raise UserError(
                    "Réponse WoS inattendue. Contacte l’administrateur."
                ) from None
            if not isinstance(data, dict):
                raise UserError("Réponse WoS inattendue. Contacte l’administrateur.")
            return data

    async def downloads(self, member: str, offset: int = 0):
        member = discord_id(member)
        return await self.request(
            "GET", f"users/discord/{member}/downloads?offset={offset}&limit=8"
        )

    async def notices(self, after=None):
        from uuid import UUID

        suffix = f"&after={UUID(after)}" if after else ""
        return await self.request("GET", f"discord/download-notices?limit=25{suffix}")

    async def capacity(self):
        data = await self.request("GET", "capacity")
        if (
            not isinstance(data.get("reached"), bool)
            or type(data.get("available")) is not int
        ):
            raise UserError("Réponse de capacité WoS inattendue.")
        return data

    async def account(self, member: str) -> Account | None:
        member = discord_id(member)
        try:
            data = await self.request("GET", f"users/discord/{member}")
        except ApiError as exc:
            if exc.status == 404 and exc.code == "discord_account_not_found":
                return None
            raise
        if not isinstance(data.get("username"), str) or any(
            type(data.get(k)) is not bool
            for k in ("is_active", "deleted", "must_change_credentials")
        ):
            raise UserError("Réponse de compte WoS inattendue.")
        return Account(
            data["username"],
            data["is_active"],
            data["deleted"],
            data["must_change_credentials"],
        )

    async def create(self, member: str, guild: str) -> Credentials:
        member, guild = discord_id(member), discord_id(guild)
        # Stable across restarts and ambiguous network failures; payload never changes.
        data = await self.request(
            "POST",
            "users",
            payload={"discord_user_id": member},
            key=f"discord-register:{guild}:{member}",
        )
        if data.get("temporary_password") is None:
            raise UserError(
                "Ton compte a déjà été créé, mais son mot de passe n’est pas renvoyé une seconde fois. "
                "Utilise « Réinitialiser mon mot de passe » pour recevoir un lien privé."
            )
        return Credentials.parse(data)

    async def reset(self, member: str) -> str:
        data = await self.request(
            "POST", f"users/discord/{discord_id(member)}/password-reset"
        )
        token = data.get("token")
        if (
            not isinstance(token, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None
            or data.get("expires_in") != 900
        ):
            raise UserError("Réponse de récupération WoS inattendue.")
        return token

    async def recover(self, member: str) -> Credentials:
        data = await self.request(
            "POST", f"users/discord/{discord_id(member)}/initial-credentials"
        )
        return Credentials.parse(data)


class Onboarding:
    def __init__(self, api: WosApi, guild: str):
        self.api, self.guild = api, discord_id(guild)

    @staticmethod
    def require_active(account: Account):
        if account.deleted:
            raise UserError("Ton compte WoS a été supprimé. Contacte l’administrateur.")
        if not account.active:
            raise UserError("Ton compte WoS est suspendu. Contacte l’administrateur.")

    async def register(self, member: str) -> Credentials:
        account = await self.api.account(member)
        if account is not None:
            self.require_active(account)
            raise UserError(
                f"Tu possèdes déjà le compte « {account.username} ». Utilise « Mon compte » "
                "ou « Réinitialiser mon mot de passe »."
            )
        if (await self.api.capacity())["reached"]:
            raise UserError(
                "Toutes les places WoS sont actuellement occupées. Réessaie plus tard."
            )
        return await self.api.create(member, self.guild)

    async def reset(self, member: str) -> str:
        account = await self.api.account(member)
        if account is None:
            raise UserError("Aucun compte WoS n’est lié à ton compte Discord.")
        self.require_active(account)
        return await self.api.reset(member)

    async def recover(self, member: str) -> Credentials:
        account = await self.api.account(member)
        if account is None:
            raise UserError(
                "Aucun compte WoS n’est lié à ton Discord. Utilise « Créer mon compte »."
            )
        self.require_active(account)
        if not account.must_change:
            raise UserError(
                "Ton compte a déjà été configuré. Contacte l’administrateur pour tes accès."
            )
        return await self.api.recover(member)

    async def status(self, member: str) -> str:
        account = await self.api.account(member)
        if account is None:
            return "Aucun compte lié à ton Discord. Utilise « Créer mon compte » pour t’inscrire."
        self.require_active(account)
        return (
            f"Ton compte WoS : **{account.username}**\nConnexion : {self.api.base_url}\n"
            "Si tu as perdu ton mot de passe, "
            "utilise « Réinitialiser mon mot de passe ». Aucun mot de passe existant n’est affiché ici."
        )
