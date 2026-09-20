"""Demo reference data, inserted on startup if the tables are empty."""

from __future__ import annotations

from sqlalchemy import select

from .db import Database
from .models import Account

ACCOUNTS = [
    ("FUND-1", "Alpha Asset Management"),
    ("CORP-2", "Beta Corporate Treasury"),
    ("HEDGE-3", "Gamma Hedge Fund"),
]


def seed_accounts(db: Database) -> None:
    with db.session() as s:
        if s.scalar(select(Account.id).limit(1)) is None:
            s.add_all(Account(id=i, name=n) for i, n in ACCOUNTS)
