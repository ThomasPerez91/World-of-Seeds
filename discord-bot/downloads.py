"""Private download views and durable, secret-free notification delivery state."""

import hashlib
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import discord

LABELS = {
    "waiting": "En attente",
    "downloading": "En téléchargement",
    "stalled": "Sans progression / sources indisponibles",
    "cooldown": "Nouvelle tentative différée",
    "ready": "Prêt à récupérer",
    "error": "Erreur",
    "cancelled": "Désabonné",
    "expired": "Expiré",
}


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def title(value):
    # Torrent metadata is untrusted, including markdown, mentions and long names.
    value = " ".join(str(value).split())[:180]
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))[:240]


def download_embed(page, url):
    embed = discord.Embed(title="Mes téléchargements WoS", color=0x34D399)
    for item in page["items"]:
        text = LABELS.get(item["state"], "État indisponible")
        if item["state"] == "waiting" and item.get("queue_position") is not None:
            text += f" · position estimée : {item['queue_position']}"
        if item["state"] in {"downloading", "stalled"}:
            text += f" · {item['progress_percent']:.1f} %"
        if item["state"] == "ready" and item.get("unsubscribe_at"):
            end = int(timestamp(item["unsubscribe_at"]).timestamp())
            text += f"\nFin de ton accès : <t:{end}:R>"
        if item.get("access_expiring_soon"):
            text += "\n🔴 Dernier tiers de ton abonnement : récupère tes fichiers rapidement."
            embed.color = discord.Color.red()
        embed.add_field(name=title(item["name"]) or "Fichier", value=text, inline=False)
    if not page["items"]:
        embed.description = "Aucun téléchargement sur cette page."
    embed.url = url
    embed.set_footer(
        text=f"{page['total']} demande(s) · page {page['offset'] // page['limit'] + 1} · position indicative, ordonnancement pondéré"
    )
    return embed


def notice_embed(item, kind, url):
    end = int(timestamp(item["unsubscribe_at"]).timestamp())
    return discord.Embed(
        title="🔴 Ton abonnement arrive à échéance"
        if kind == "warning"
        else "✅ Ton fichier est prêt",
        description=(
            f"**{title(item['name'])}**\n\n"
            + (
                "Tu entres dans le dernier tiers de ta durée d’accès. Récupère tes fichiers avant son expiration.\n\n"
                if kind == "warning"
                else "Ton téléchargement est terminé. Tu peux récupérer tes fichiers sur WoS.\n\n"
            )
            + f"Fin de ton accès : <t:{end}:F> (<t:{end}:R>)."
        ),
        color=discord.Color.red() if kind == "warning" else 0x34D399,
        url=url,
    )


class DeliveryState:
    def __init__(self, directory: Path):
        self.closed = False
        self.db = sqlite3.connect(directory / "notifications.sqlite3")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS delivered (key TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS preferences (member TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT);
        """)
        self.db.commit()

    def enabled(self, member):
        row = self.db.execute(
            "SELECT enabled FROM preferences WHERE member=?", (member,)
        ).fetchone()
        return row is None or bool(row[0])

    def toggle(self, member):
        enabled = not self.enabled(member)
        self.db.execute(
            "INSERT OR REPLACE INTO preferences VALUES (?, ?)", (member, int(enabled))
        )
        self.db.commit()
        return enabled

    def cursor(self):
        row = self.db.execute("SELECT value FROM cursor WHERE id=1").fetchone()
        return row[0] if row else None

    def advance(self, value):
        self.db.execute("INSERT OR REPLACE INTO cursor VALUES (1, ?)", (value,))
        self.db.execute(
            "DELETE FROM delivered WHERE expires < ?", (time.time() - 86400,)
        )
        self.db.commit()

    def claim(self, item, kind):
        # READY timestamp distinguishes resubscriptions reusing the same request ID.
        # Include deadline only for warnings, so an extension doesn't resend READY.
        raw = f"{item['request_id']}:{item['ready_at']}:{kind}"
        if kind == "warning":
            raw += ":" + item["unsubscribe_at"]
        key = hashlib.sha256(raw.encode()).hexdigest()
        result = self.db.execute(
            "INSERT OR IGNORE INTO delivered VALUES (?, ?)",
            (key, timestamp(item["unsubscribe_at"]).timestamp()),
        )
        self.db.execute(
            "UPDATE delivered SET expires = max(expires, ?) WHERE key = ?",
            (timestamp(item["unsubscribe_at"]).timestamp(), key),
        )
        self.db.commit()
        return result.rowcount == 1

    def close(self):
        if not self.closed:
            self.db.close()
            self.closed = True
