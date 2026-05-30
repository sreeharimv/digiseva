"""
One-time migration: digiseva.json → digiseva.db (SQLite)

Run automatically on first startup if the JSON file exists.
The original JSON is renamed to digiseva.json.bak after a successful migration.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

JSON_PATH = os.environ.get("DATA_PATH", "/app/data/digiseva.json")


def run_migration() -> int:
    """Migrate JSON data to SQLite. Returns count of services migrated (0 if nothing to do)."""
    if not os.path.exists(JSON_PATH):
        return 0

    from database import init_db
    from storage import add_service
    from models import Service

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    services = data.get("services", [])
    if not services:
        logger.info("Migration: JSON file exists but has no services — skipping")
        return 0

    init_db()
    count, errors = 0, []

    for s in services:
        try:
            # Fill in defaults for fields that didn't exist in the old schema
            s.setdefault("tenure_months", None)
            s.setdefault("paid_instalments", 0)
            s.setdefault("credit_limit", None)
            s.setdefault("outstanding_balance", 0.0)
            s.setdefault("statement_amount", 0.0)
            add_service(Service(**s))
            count += 1
        except Exception as e:
            errors.append(f"  {s.get('name', '?')}: {e}")

    if errors:
        logger.warning(f"Migration: {len(errors)} row(s) skipped:\n" + "\n".join(errors))

    # Keep the original JSON as a backup
    backup_path = JSON_PATH + ".bak"
    os.rename(JSON_PATH, backup_path)
    logger.info(
        f"Migration complete: {count} service(s) moved to SQLite. "
        f"Original JSON backed up to {backup_path}"
    )
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    n = run_migration()
    print(f"Migrated {n} service(s)." if n else "Nothing to migrate.")
