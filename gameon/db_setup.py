"""Run python -m gameon.db_setup --env-file .env.preview.local --schema gameon_preview."""
import argparse
from dotenv import dotenv_values
from .postgres import PostgresStore
from .storage import DatabaseUnavailable


def main():
    parser = argparse.ArgumentParser(description="Initialise only the explicitly selected PostgreSQL schema")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--schema", required=True)
    args = parser.parse_args()
    config = dotenv_values(args.env_file)
    url = config.get("DATABASE_URL_UNPOOLED") or config.get("DATABASE_URL")
    if not url:
        parser.error("Database URL is missing from the selected environment file")
    try:
        PostgresStore(url, args.schema).initialise()
    except DatabaseUnavailable:
        raise SystemExit("Schema setup failed; connection details suppressed.")
    print("Schema ready:", args.schema)


if __name__ == "__main__":
    main()
