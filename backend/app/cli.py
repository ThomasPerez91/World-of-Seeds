import argparse
import asyncio
import getpass
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.security import canonical_username, hash_password, normalize_username
from app.core.config import get_settings
from app.core.database import session_factory
from app.files import WorkspaceError, WorkspaceManager
from app.models import User
from app.v1_import import (
    V1ImportConflictError,
    V1ImportError,
    apply_v1_import,
    export_v1_inventory,
    load_inventory,
    plan_report,
    plan_v1_import,
    rollback_v1_import,
    write_private_report,
)


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


async def inventory_v1(source_url_file: Path, output: Path, snapshot_id: str) -> None:
    try:
        inventory = await export_v1_inventory(source_url_file, output, snapshot_id)
    except V1ImportError as exc:
        raise SystemExit(f"Inventaire V1 refusé : {exc}") from exc
    print(
        f"Inventaire V1 privé créé : {len(inventory.rows)} ligne(s), "
        f"empreinte {inventory.fingerprint}."
    )


async def import_v1(
    inventory_path: Path,
    report_path: Path,
    *,
    apply: bool,
    confirmed_fingerprint: str | None,
    backup_id: str | None,
) -> None:
    try:
        inventory = load_inventory(inventory_path)
        if not apply:
            async with session_factory() as db:
                plan = await plan_v1_import(db, inventory)
            write_private_report(report_path, plan_report(plan, mode="dry-run"))
            print(
                f"Dry-run V1 terminé : {len(plan.actions)} ligne(s), "
                f"{plan.conflict_count} conflit(s)."
            )
            return
        if confirmed_fingerprint != inventory.fingerprint:
            raise V1ImportError("confirmed source fingerprint does not match the inventory")
        if backup_id is None:
            raise V1ImportError("a restored V2 backup ID is required before apply")
        try:
            async with session_factory() as db, db.begin():
                result = await apply_v1_import(db, inventory, backup_id=backup_id)
        except V1ImportConflictError as exc:
            write_private_report(report_path, plan_report(exc.plan, mode="apply-blocked"))
            raise V1ImportError("conflicts blocked the import; inspect the private report") from exc
        mode = "idempotent-replay" if result.idempotent_replay else "applied"
        write_private_report(
            report_path,
            plan_report(result.plan, mode=mode, run_id=result.run_id),
        )
        print(f"Import V1 {mode} : run {result.run_id}.")
    except V1ImportError as exc:
        raise SystemExit(f"Import V1 refusé : {exc}") from exc


async def rollback_v1(run_id_text: str, confirmation: str, report_path: Path) -> None:
    try:
        run_id = uuid.UUID(run_id_text)
    except ValueError as exc:
        raise SystemExit("Identifiant de run V1 invalide.") from exc
    if confirmation != str(run_id):
        raise SystemExit("La confirmation ne correspond pas au run V1.")
    try:
        async with session_factory() as db, db.begin():
            report = await rollback_v1_import(db, run_id)
        write_private_report(report_path, report)
    except V1ImportError as exc:
        raise SystemExit(f"Rollback V1 refusé : {exc}") from exc
    print(f"Rollback V1 : {report['result']}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="world-of-seeds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin_parser = subparsers.add_parser("create-admin")
    create_admin_parser.add_argument("--username", required=True)
    subparsers.add_parser("migrate-workspaces")
    inventory_parser = subparsers.add_parser("inventory-v1")
    inventory_parser.add_argument("--source-url-file", type=Path, required=True)
    inventory_parser.add_argument("--snapshot-id", required=True)
    inventory_parser.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import-v1")
    import_parser.add_argument("--inventory", type=Path, required=True)
    import_parser.add_argument("--report", type=Path, required=True)
    import_parser.add_argument("--apply", action="store_true")
    import_parser.add_argument("--confirm-source-fingerprint")
    import_parser.add_argument("--backup-id")
    rollback_parser = subparsers.add_parser("rollback-v1-import")
    rollback_parser.add_argument("--run-id", required=True)
    rollback_parser.add_argument("--confirm-run-id", required=True)
    rollback_parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "create-admin":
        asyncio.run(create_admin(args.username))
    elif args.command == "migrate-workspaces":
        asyncio.run(migrate_workspaces())
    elif args.command == "inventory-v1":
        asyncio.run(inventory_v1(args.source_url_file, args.output, args.snapshot_id))
    elif args.command == "import-v1":
        asyncio.run(
            import_v1(
                args.inventory,
                args.report,
                apply=args.apply,
                confirmed_fingerprint=args.confirm_source_fingerprint,
                backup_id=args.backup_id,
            )
        )
    elif args.command == "rollback-v1-import":
        asyncio.run(rollback_v1(args.run_id, args.confirm_run_id, args.report))


if __name__ == "__main__":
    main()
