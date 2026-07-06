#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_client.py — hirdetmenyek.gov.hu JSON API kliens
---------------------------------------------------
Végpont: https://hirdetmenyek.gov.hu/api/hirdetmenyek/reszletezo/{id}
Nyilvános, hitelesítés nem kell. Szerverkímélet: egyetlen szál,
kérésenként késleltetés + jitter, retry csak 5xx-re, 403/429-re AZONNALI
leállás (RateLimitedError).
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from zoneinfo import ZoneInfo
    BUDAPEST = ZoneInfo("Europe/Budapest")
except Exception:  # noqa: BLE001
    BUDAPEST = None

API_TMPL = "https://hirdetmenyek.gov.hu/api/hirdetmenyek/reszletezo/{id}"
REQUEST_DELAY = 1.0
REQUEST_JITTER = 0.5
REQUEST_TIMEOUT = 15

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hu-HU,hu;q=0.9,en;q=0.8",
    "User-Agent": (
        "hirdetmeny-terkep/1.0 (+https://github.com/lukacspeter89/hirdetmenyek; "
        "nyilt forrasu terkep-projekt; kapcsolat: peter.lukacs@bimline.hu)"
    ),
}


class RateLimitedError(Exception):
    """HTTP 403/429 — a futásnak azonnal le kell állnia."""


def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=3, backoff_factor=2.0,
        status_forcelist=(500, 502, 503, 504),   # 429 szándékosan NINCS itt
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def polite_sleep() -> None:
    time.sleep(REQUEST_DELAY + random.uniform(0, REQUEST_JITTER))


def fetch_detail(session: requests.Session, ad_id: int):
    """Egy hirdetmény lekérése. None = nem létező ID (üres válasz / 404)."""
    url = API_TMPL.format(id=ad_id)
    resp = session.get(
        url, timeout=REQUEST_TIMEOUT,
        headers={"Referer": f"https://hirdetmenyek.gov.hu/reszletezo/{ad_id}"},
    )
    if resp.status_code in (403, 429):
        raise RateLimitedError(f"HTTP {resp.status_code} id={ad_id}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.text.strip()
    if not body:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


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
  