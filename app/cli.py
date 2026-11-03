"""Command-line helpers, run inside the container:

    docker exec -it stories-and-videos python -m app.cli add-user Mom
    docker exec -it stories-and-videos python -m app.cli set-password Mom
    docker exec -it stories-and-videos python -m app.cli list-users
    docker exec -it stories-and-videos python -m app.cli scan
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from . import auth
from .config import Config
from .db import Database
from .scanner import Scanner


def _ask_password() -> str:
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Type it again: "):
        raise SystemExit("The two passwords did not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-user", help="add a person")
    add.add_argument("username")
    add.add_argument("--name", default="", help="name shown on screen, e.g. 'Mom' (defaults to the username)")
    add.add_argument("--admin", action="store_true", help="allow them to manage people")
    add.add_argument("--password", help="password (you will be asked if left out)")

    setpw = sub.add_parser("set-password", help="change someone's password")
    setpw.add_argument("username")
    setpw.add_argument("--password", help="new password (you will be asked if left out)")

    sub.add_parser("list-users", help="show everyone who can sign in")
    sub.add_parser("scan", help="look for new videos now and print the result")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    cfg = Config.from_env()
    db = Database(cfg.data_dir / "stories.db")
    conn = db.connect()

    try:
        if args.command == "add-user":
            auth.create_user(conn, args.username, args.password or _ask_password(), args.name, args.admin)
            print(f"Added {args.username}.")
        elif args.command == "set-password":
            user = auth.find_user(conn, args.username)
            if user is None:
                raise SystemExit(f"There is no user called {args.username!r}.")
            auth.set_password(conn, user["id"], args.password or _ask_password())
            print(f"Changed the password for {user['username']}.")
        elif args.command == "list-users":
            for row in conn.execute("SELECT username, display_name, is_admin FROM users ORDER BY username"):
                print(f"{row['username']:<20} {row['display_name']:<20} {'admin' if row['is_admin'] else ''}")
        elif args.command == "scan":
            status = Scanner(cfg, db).scan_once()
            print(f"Found {status.found} videos: {status.added} new, {status.updated} changed, "
                  f"{status.moved} moved, {status.waiting} still copying, {len(status.unreadable)} unreadable.")
            if status.error:
                print(status.error, file=sys.stderr)
                return 1
    except auth.UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
