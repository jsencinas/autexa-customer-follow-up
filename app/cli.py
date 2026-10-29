"""Command line tools: `uv run python -m app.cli create-user --username ana --role admin`."""

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
    args = parser.parse_args(argv)

    session = init_db(get_settings().database_url)()
    if args.command == "create-user":
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
        try:
            user = auth.create_user(session, args.username, password, Role(args.role))
        except auth.UserError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created {user.role} '{user.username}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
