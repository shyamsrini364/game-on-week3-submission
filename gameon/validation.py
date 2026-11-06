"""Shared storage validation and safe public errors."""
import secrets


class StorageError(ValueError):
    pass


class DatabaseUnavailable(RuntimeError):
    """Safe public error: never include database connection details."""


def require_text(value, label):
    if not isinstance(value, str):
        raise StorageError(f"{label} must be text.")
    return value.strip()


def _new_id() -> str:
    return secrets.token_urlsafe(9)


