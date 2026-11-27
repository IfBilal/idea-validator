"""Retention of ``scouts.raw_pages``: processed rows are copies of what M6 already holds."""

from datetime import datetime, timedelta

from .ports import RawStore


async def prune_processed(store: RawStore, *, older_than_days: int, now: datetime) -> int:
    """Delete processed rows last fetched more than ``older_than_days`` ago; returns the count."""
    if older_than_days < 1:
        raise ValueError("older_than_days must be >= 1")
    return await store.prune(now - timedelta(days=older_than_days))
