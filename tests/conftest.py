import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Unit tests must never download models. Outside RUN_SLOW, force Hugging Face offline so an
# accidental real model load fails fast instead of downloading.
if os.environ.get("RUN_SLOW") != "1":
    os.environ["HF_HUB_OFFLINE"] = "1"


@pytest.fixture
def repo() -> Path:
    return REPO
