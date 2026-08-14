import argparse
import asyncio
import getpass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.security import canonical_username, hash_password, normalize_username
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
        existing = await db.scalar(
            select(User).where(func.lower(User.username) == canonical_username(username))
        )
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
        except IntegrityError as exc:
            raise SystemExit("Ce nom d’utilisateur existe déjà.") from exc
        except WorkspaceError as exc:
            raise SystemExit(
                "Impossible de créer l’espace de stockage de l’administrateur."
            ) from exc
    print(f"Administrateur {username!r} créé.")


async def migrate_workspaces() -> None:
    """Move legacy workspaces and reconcile their versioned directory structure."""

    workspace_manager = WorkspaceManager(get_settings().data_root)
    async with session_factory() as db:
        users = (await db.scalars(select(User).order_by(User.username))).all()

    moved = 0
    already_ready = 0
    retired_removed = 0
    retired_retained = 0
    for user in users:
        try:
            if workspace_manager.migrate_legacy(user.username):
                moved += 1
                print(f"Espace {user.username!r} migré.")
            else:
                already_ready += 1
                print(f"Espace {user.username!r} déjà prêt.")
            cleanup = workspace_manager.cleanup_retired_directories(user.username)
            retired_removed += len(cleanup.removed)
            retired_retained += len(cleanup.retained)
            for directory in cleanup.removed:
                print(f"Ancien dossier {user.username!r}/{directory} vide supprimé.")
            for directory in cleanup.retained:
                print(f"Ancien dossier {user.username!r}/{directory} conservé car non vide.")
        except WorkspaceError as exc:
            raise SystemExit(f"Migration interrompue pour {user.username!r}: {exc}") from exc

    legacy_root_removed = workspace_manager.remove_legacy_root_if_empty()
    print(
        f"Migration terminée : {moved} déplacé(s), "
        f"{already_ready} déjà prêt(s), "
        f"{retired_removed} ancien(s) dossier(s) vide(s) supprimé(s), "
        f"{retired_retained} ancien(s) dossier(s) non vide(s) conservé(s), "
        f"ancien dossier supprimé : {'oui' if legacy_root_removed else 'non'}."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="world-of-seeds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin_parser = subparsers.add_parser("create-admin")
    create_admin_parser.add_argument("--username", required=True)
    subparsers.add_parser("migrate-workspaces")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "create-admin":
        asyncio.run(create_admin(args.username))
    elif args.command == "migrate-workspaces":
        asyncio.run(migrate_workspaces())


if __name__ == "__main__":
    main()
