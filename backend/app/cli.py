import argparse
import asyncio
import getpass

from sqlalchemy import select

from app.auth.security import hash_password, normalize_username
from app.core.config import get_settings
from app.core.database import session_factory
from app.files import WorkspaceError, WorkspaceManager
from app.models import User


async def create_admin(username_input: str) -> None:
    username = normalize_username(username_input)
    password = getpass.getpass("Mot de passe administrateur : ")
    confirmation = getpass.getpass("Confirmez le mot de passe : ")
    if password != confirmation:
        raise SystemExit("Les mots de passe ne correspondent pas.")

    async with session_factory() as db:
        existing = await db.scalar(select(User).where(User.username == username))
        if existing is not None:
            raise SystemExit("Ce nom d’utilisateur existe déjà.")
        workspace_manager = WorkspaceManager(get_settings().data_root)
        try:
            with workspace_manager.provision_for_transaction(username):
                db.add(
                    User(
                        username=username,
                        password_hash=hash_password(password),
                        is_admin=True,
                        must_change_credentials=False,
                    )
                )
                try:
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
        except WorkspaceError as exc:
            raise SystemExit(
                "Impossible de créer l’espace de stockage de l’administrateur."
            ) from exc
    print(f"Administrateur {username!r} créé.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="world-of-seeds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin_parser = subparsers.add_parser("create-admin")
    create_admin_parser.add_argument("--username", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "create-admin":
        asyncio.run(create_admin(args.username))


if __name__ == "__main__":
    main()
