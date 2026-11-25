"""Bootstrap and inspection from the server's shell (there is no API to make the first super admin).

    PYTHONPATH=app uv run python -m rbac.cli grant-super-admin persona@cooperativa.com
    PYTHONPATH=app uv run python -m rbac.cli revoke-super-admin persona@cooperativa.com
    PYTHONPATH=app uv run python -m rbac.cli list-super-admins
    PYTHONPATH=app uv run python -m rbac.cli effective persona@cooperativa.com

The person must have signed in once (users are only created by a Microsoft 365 login).
"""
import argparse
import json
import sys

from dotenv import load_dotenv

CLI_ACTOR = {"sub": "cli"}


def _user(service, email):
    user = service.find_user_by_email(email)
    if user is None:
        sys.exit(f"No user with email {email}: they must sign in with Microsoft 365 once first.")
    return user


def main(argv=None):
    load_dotenv()
    import capabilities.registry  # noqa: F401  (lets `effective` list the registered capabilities)
    import providers.registry  # noqa: F401  (and check model ids)
    from rbac import audit, service
    from rbac.catalog import SUPER_ADMIN

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("grant-super-admin", "revoke-super-admin", "effective"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list-super-admins")
    args = parser.parse_args(argv)

    role = next((r for r in service.list_roles() if r["key"] == SUPER_ADMIN), None)
    if role is None:
        sys.exit("Role super_admin not found: apply the migrations first (python -m db.db_setup --apply).")

    if args.command == "list-super-admins":
        ids = set(service.role_holders(role["id"]))
        active = set(service.active_super_admins())
        for user_id in sorted(ids):
            print(user_id, "active" if user_id in active else "inactive or expired")
        return

    user = _user(service, args.email)
    if args.command == "grant-super-admin":
        service.assign_role(user["id"], role["id"], by="cli")
        audit.record(CLI_ACTOR, "role.assign", "user", user["id"], details={"role": SUPER_ADMIN})
        print(f"{args.email} is now super_admin ({user['status']}).")
    elif args.command == "revoke-super-admin":
        service.revoke_role(user["id"], role["id"])
        audit.record(CLI_ACTOR, "role.revoke", "user", user["id"], details={"role": SUPER_ADMIN})
        print(f"{args.email} is no longer super_admin.")
    elif args.command == "effective":
        access = service.access_for(user["id"])
        print(json.dumps(access.explain(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
