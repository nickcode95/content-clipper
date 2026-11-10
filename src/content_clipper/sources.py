"""data/sources/registry.csv: Nick's hand-kept record of footage each campaign authorises."""

from __future__ import annotations

import csv
from pathlib import Path

REGISTRY = Path("data/sources/registry.csv")


def is_registered(video_id: str, campaign: str, registry: Path = REGISTRY) -> bool:
    if not registry.exists():
        return False
    with registry.open(newline="", encoding="utf-8") as f:
        return any(
            (row.get("video_id") or "").strip() == video_id
            and (row.get("campaign") or "").strip() == campaign
            for row in csv.DictReader(f)
        )
