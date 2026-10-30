"""Command line tools.

    uv run python -m app.cli create-user --username ana --role admin
    uv run python -m app.cli set-password --username ana
"""

import argparse
import getpass
import sys

from app.config import get_settings
from app.db import init_db
from app.models.enums import Role
from app.services import auth


def main(argv: list[str] | None = None) -> int:
    """Entry point for the CLI."""
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="create a web app account")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.EMPLOYEE.value)
    reset = sub.add_parser("set-password", help="set a new password for an existing account")
    reset.add_argument("--username", required=True)
    args = parser.parse_args(argv)

    session = init_db(get_settings().database_url)()
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if args.command == "set-password":
        try:
            user = auth.set_password(session, args.username, password)
        except auth.UserError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Password updated for '{user.username}'.")
    elif args.command == "create-user":
        try:
            user = auth.create_user(session, args.username, password, Role(args.role))
        except auth.UserError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created {user.role} '{user.username}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
