#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_client.py — a gov.hu részletező JSON → egységes rekord parser
-----------------------------------------------------------------
A gyűjtést (gov.hu hívások) a NAS végzi (lakossági IP, curl), és a nyers
API-válaszokat a raw/items/<id>.json fájlokba menti. Az Actions-oldalon
csak a feldolgozás fut, ezért itt már CSAK a parse_detail kell: a process.py
ezzel alakítja a nyers JSON-t egységes rekorddá.

(A korábbi curl_cffi-alapú letöltő — build_session/fetch_detail/polite_sleep/
RateLimitedError — átkerült a NAS-ra (nas/hirdetmeny.sh), ezért innen törölve.)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    BUDAPEST = ZoneInfo("Europe/Budapest")
except Exception:  # noqa: BLE001
    BUDAPEST = None


# --------------------------------------------------------------------------- #
# JSON → rekord
# --------------------------------------------------------------------------- #
def _collect(attrs: dict, base: str) -> list:
    """Számozott attribútumok (base1, base2, ...) növekvő sorrendben."""
    items = []
    for k, v in attrs.items():
        m = re.fullmatch(rf"{re.escape(base)}(\d+)", k)
        if m and v not in (None, ""):
            items.append((int(m.group(1)), str(v).strip()))
    items.sort()
    return [v for _, v in items]


def _collect_price(attrs: dict) -> list:
    """Ár földrészletenként: adás-vételnél vetelarN, haszonbérletnél haszonberN."""
    by_idx = {}
    for base in ("vetelar", "haszonber"):
        for k, v in attrs.items():
            m = re.fullmatch(rf"{base}(\d+)", k)
            if m and v not in (None, ""):
                by_idx.setdefault(int(m.group(1)), str(v).strip())
    return [by_idx[i] for i in sorted(by_idx)]


def _iso_local_date(s):
    """ISO datetime (UTC) → budapesti dátum ÉÉÉÉ-HH-NN."""
    if not s:
        return None
    txt = str(s).replace("Z", "+00:00")
    dt = None
    for candidate in (txt, re.sub(r"\.\d+", "", txt)):
        try:
            dt = datetime.fromisoformat(candidate)
            break
        except ValueError:
            continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if BUDAPEST is not None:
        dt = dt.astimezone(BUDAPEST)
    return dt.date().isoformat()


def _clean_telepules(raw: str):
    """'Telkibánya, külterület ' → 'Telkibánya'."""
    if not raw:
        return None
    t = raw.split(",")[0].strip()
    t = re.sub(r"\s+(kül|bel)terület.*$", "", t, flags=re.IGNORECASE).strip()
    return t or None


def parse_detail(ad_id: int, data: dict) -> dict:
    """A részletező JSON-ból egységes rekord (normalizálás nélkül)."""
    dto = data.get("hirdetmenyDTO") or {}
    attrs = data.get("attributumok") or {}

    altipus = data.get("altipus") or ""
    if "adás-vétel" in altipus:
        tipus = "adasvetel"
    elif "haszonbér" in altipus:
        tipus = "haszonberlet"
    else:
        tipus = "egyeb"

    telepulesek = _collect(attrs, "telepules")
    telepules = _clean_telepules(telepulesek[0]) if telepulesek else None
    if not telepules:
        # tartalék: a tárgy mezőből ("Adás-vétel - Lepsény hrsz.: 1698")
        m = re.match(r".+?\s+-\s+(.+?)\s+hrsz", dto.get("targy") or "")
        telepules = _clean_telepules(m.group(1)) if m else None

    return {
        "id": ad_id,
        "kategoria": dto.get("kategoria"),
        "tipus": tipus,
        "altipus": altipus or None,
        "targy": dto.get("targy"),
        "telepules": telepules,
        "hrsz": _collect(attrs, "helyrajzi_szam"),
        "muvelesi_ag": sorted(set(_collect(attrs, "muvelesi_ag"))),
        "tulajdoni_hanyad": _collect(attrs, "tulhanyad"),
        "ar_raw": _collect_price(attrs),
        "terulet_raw": _collect(attrs, "terulet"),
        "kifuggesztes": _iso_local_date(dto.get("kifuggesztesNapja")),
        "lejarat": _iso_local_date(dto.get("lejaratNapja")),
        "forras": dto.get("forrasIntezmenyNeve"),
        "ugyiratszam": dto.get("ugyiratszam") or dto.get("iktatasiszam"),
        "link": f"https://hirdetmenyek.gov.hu/reszletezo/{ad_id}",
        "csatolmanyok": [c.get("id") for c in (data.get("csatolmanyok") or [])
                         if c.get("id") is not None],
    }
