"""Manage sign-in accounts from the command line.

    python -m vajra.users add <user-id> --name "Full Name" --role forecaster
    python -m vajra.users list
    python -m vajra.users set-password <user-id>
    python -m vajra.users unlock <user-id>
    python -m vajra.users remove <user-id>

The password is typed at a prompt and never shown. For scripted use, pass
--password-stdin and supply it on standard input.
"""

import argparse
import getpass
import sys

from vajra import auth
from vajra.config import get_settings


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("the two passwords do not match")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage VAJRA sign-in accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="create an account")
    add.add_argument("username")
    add.add_argument("--name", default="", help="name shown in the app and recorded against decisions")
    add.add_argument("--role", choices=auth.ROLES, default="forecaster")
    add.add_argument("--password-stdin", action="store_true")
    commands.add_parser("list", help="show all accounts")
    change = commands.add_parser("set-password", help="change a password; unlocks and signs the account out")
    change.add_argument("username")
    change.add_argument("--password-stdin", action="store_true")
    commands.add_parser("unlock", help="unlock an account locked by failed sign-ins").add_argument("username")
    commands.add_parser("remove", help="delete an account").add_argument("username")
    args = parser.parse_args()
    settings = get_settings()
    settings.ensure_dirs()

    try:
        if args.command == "add":
            user = auth.create_user(
                settings, args.username, args.name, args.role, _read_password(args.password_stdin)
            )
            print(f"created '{user.username}' ({user.display_name}), role {user.role}")
        elif args.command == "list":
            rows = auth.list_users(settings)
            if not rows:
                print("no accounts exist; create one with: python -m vajra.users add <user-id>")
            for row in rows:
                locked = "  LOCKED" if row["locked_until"] else ""
                print(f"{row['username']:20s} {row['role']:11s} {row['display_name']}{locked}"
                      f"   last sign-in: {row['last_login_at'] or 'never'}")
        elif args.command == "set-password":
            auth.set_password(settings, args.username.strip().lower(), _read_password(args.password_stdin))
            print(f"password changed for '{args.username}'")
        elif args.command == "unlock":
            auth.unlock(settings, args.username.strip().lower())
            print(f"'{args.username}' unlocked")
        elif args.command == "remove":
            auth.remove_user(settings, args.username.strip().lower())
            print(f"'{args.username}' removed")
    except auth.AuthError as err:
        raise SystemExit(str(err)) from err


if __name__ == "__main__":
    main()
