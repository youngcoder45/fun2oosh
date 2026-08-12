"""Base model class for all database models."""

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


def utcnow() -> datetime:
    """Naive UTC now, matching the values stored by the models.

    ``datetime.utcnow()`` is deprecated since Python 3.12; this helper keeps
    the same naive-UTC semantics without the deprecation warning.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
