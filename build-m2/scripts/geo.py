#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geo.py — település → koordináta / megye / járás
-----------------------------------------------
Lusta geokódolás Nominatim-mal (OSM), tartós cache-sel:
minden települést pontosan EGYSZER kérdezünk le, utána a repo
data/geo/telepulesek.json fájljából jön. A Nominatim használati
szabályzata szerint max. 1 kérés/mp, azonosító User-Agenttel.
Futásonként legfeljebb GEOCODE_PER_RUN új település — a többi a
következő futásra marad (nem blokkolja a gyűjtést).
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from store import _read_json, _write_json  # noqa: E402

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEOCODE_PER_RUN = 15
GEO_PATH = Path(__file__).resolve().parent.parent / "data" / "geo" / "telepulesek.json"

UA = ("hirdetmeny-terkep/1.0 (nyilt forrasu terkep-projekt; "
      "kapcsolat: peter.lukacs@bimline.hu)")


def load_geo() -> dict:
    return _read_json(GEO_PATH, {})


def save_geo(geo: dict) -> None:
    _write_json(GEO_PATH, geo)


def _query(name: str):
    resp = requests.get(
        NOMINATIM_URL,
        params={
            "q": name, "countrycodes": "hu", "format": "jsonv2",
            "addressdetails": 1, "limit": 1,
            "featureType": "settlement",
        },
        headers={"User-Agent": UA},
        timeout=20,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    r = results[0]
    addr = r.get("address", {})
    return {
        "lat": round(float(r["lat"]), 5),
        "lon": round(float(r["lon"]), 5),
        "megye": addr.get("county"),
        "jaras": addr.get("district") or addr.get("state_district"),
    }


def geocode_missing(telepulesek: set, geo: dict, limit: int = GEOCODE_PER_RUN) -> int:
    """Hiányzó települések geokódolása (max. limit db / futás).
    A sikertelen keresés is cache-elődik (None), nem próbáljuk örökké."""
    todo = sorted(t for t in telepulesek if t and t not in geo)[:limit]
    for name in todo:
        try:
            geo[name] = _query(name)
        except requests.RequestException:
            break            # hálózati gond → majd a következő futás
        time.sleep(1.1)      # Nominatim: max 1 kérés/mp
    return len(todo)
