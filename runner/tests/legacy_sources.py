"""Frozen source bytes for the historical, SHA-pinned activation tools."""
from pathlib import Path
import gzip
import hashlib

PINS = {"manager":"81aa1f6405f71ebf15740079bbde89c8f4e7d44820110172a6ad1565728591ec",
        "helper":"addf16e83af68ecfd99fcc080dfa93f8f04e2c8e57098f091c5688c100be6954"}

def legacy_source(name):
    data = gzip.decompress((Path(__file__).with_name("fixtures") / "legacy-four-pool" / (name + ".py.gz")).read_bytes())
    assert hashlib.sha256(data).hexdigest() == PINS[name], "Frozen legacy activation fixture changed"
    return data
