"""Command-line entry points: python -m app.cli <command>

  seed     create the demo tenants (images + posts are registered here once the corpus lands)
"""
import argparse
import logging

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.logging_config import configure_logging
from app.repositories import tenants

log = logging.getLogger("app.cli")

DEMO_TENANTS = {
    "demo": "Demo tenant (all probes run here)",
    "other": "Second tenant, used to prove tenant isolation",
}


def seed() -> None:
    with get_sessionmaker()() as session, session.begin():
        for tenant_id, name in DEMO_TENANTS.items():
            tenants.upsert(session, tenant_id, name)
    log.info("Seeded tenants: %s", ", ".join(DEMO_TENANTS))


def main() -> None:
    configure_logging(get_settings())
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="create demo tenants (idempotent)")
    args = parser.parse_args()

    if args.command == "seed":
        seed()


if __name__ == "__main__":
    main()
