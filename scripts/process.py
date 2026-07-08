#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
process.py — a NAS által letöltött nyers JSON-ból építi a térkép-adatot.
------------------------------------------------------------------------
A gyűjtés (gov.hu hívások) a NAS-on történik (lakossági IP), és a nyers
API-válaszokat a repo raw/items/<id>.json fájljaiba menti. Ez a szkript
a GitHub Actions-ön fut: beolvassa a nyers JSON-okat, normalizál,
geokódol (Nominatim/OSM — nem gov.hu), és felépíti a data/ könyvtárat,
amit a térkép (index.html) használ.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import geo as geomod  # noqa: E402
from api_client import parse_detail  # noqa: E402
from normalize import normalize_item  # noqa: E402
from store import Store  # noqa: E402

RAW_ITEMS = Path(__file__).resolve().parent.parent / "raw" / "items"


def run() -> int:
    st = Store()
    n = 0
    if RAW_ITEMS.exists():
        for f in sorted(RAW_ITEMS.glob("*.json")):
            try:
                ad_id = int(f.stem)
            except ValueError:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            record = parse_detail(ad_id, data)
            if record.get("kategoria") != "Föld":
                continue
            record.update(normalize_item(
                record["ar_raw"], record["terulet_raw"],
                record.get("altipus") or ""))
            st.put_item(record)
            n += 1

    # geokódolás (Nominatim/OSM; nem gov.hu — a felhőből is megy)
    telepulesek = set()
    for month in list(st._partitions.values()):
        telepulesek.update(r.get("telepules") for r in month.values())
    geo = geomod.load_geo()
    n_geo = geomod.geocode_missing({t for t in telepulesek if t}, geo)
    if n_geo:
        geomod.save_geo(geo)

    st.save()
    print(f"Feldolgozva: {n} Föld-tétel | új geokód: {n_geo}")
    print("Állapot:", st.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
