"""CLI: create an API key for ediarum.fileservice.

    ediarum-fileservice-create-key <project-slug> [--created-by NAME]
    ediarum-fileservice-create-key --admin [--created-by NAME]

Prints the plaintext key once — only its hash is ever stored (see
``ediarum_sdk.auth.create_key``). This is how `ediarum.webdav`'s
per-project ``ediarum_backend_api_key`` gets minted.
"""

import argparse

from sqlmodel import Session

from ediarum_sdk.auth import create_key

from . import db
from .config import Settings


def main() -> None:
    "``ediarum-fileservice-create-key``: create a key and print it."
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", help="project slug this key is scoped to (omit with --admin)")
    parser.add_argument("--admin", action="store_true", help="an admin key, valid for every project")
    parser.add_argument("--name", help="a name for the key (default: derived from the project slug)")
    parser.add_argument("--created-by", help="who's creating this (for audit)")
    args = parser.parse_args()
    if not args.admin and not args.project:
        parser.error("pass a project slug, or --admin")

    settings = Settings.from_env()
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)

    name = args.name or (f"admin-{args.created_by or 'key'}" if args.admin else args.project)
    projects = "*" if args.admin else args.project
    with Session(engine) as session:
        key = create_key(session, name, projects=projects, is_admin=args.admin, created_by=args.created_by)

    print(key)


if __name__ == "__main__":
    main()
