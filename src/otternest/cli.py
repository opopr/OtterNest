"""Explicit operator-only database bootstrap. No migrations are run at HTTP startup."""
import argparse

from .db import Base, get_engine
from . import models  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(description="OtterNest administration")
    parser.add_argument("command", choices=["init-db"])
    args = parser.parse_args()
    if args.command == "init-db":
        Base.metadata.create_all(get_engine())
        print("Schema initialized. Do not use init-db as a schema migration tool.")


if __name__ == "__main__":
    main()
