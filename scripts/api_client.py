#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api_client.py — hirdetmenyek.gov.hu JSON API kliens
---------------------------------------------------
Végpont: https://hirdetmenyek.gov.hu/api/hirdetmenyek/reszletezo/{id}
Nyilvános, hitelesítés nem kell. A gov.hu elé F5 bot-védelem került, ami a
nem-böngésző klienseket (sima requests/curl) a TLS-ujjlenyomatuk alapján
403-mal utasítja el. Ezért curl_cffi-vel böngésző (Chrome/Safari) TLS-
ujjlenyomatot imitálunk, egy "bemelegítő" főoldal-letöltéssel megszerezzük a
terheléselosztó sütijét, és a böngésző kliens-tipp (sec-ch-ua) fejléceit
küldjük. Egyetlen szál, kérésenként késleltetés + jitter, 5xx-re néhány
újrapróbálás, 403/429-re azonnali leállás (RateLimitedError).
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone

import requests  # csak a hívók által elkapott kivétel-típusokhoz (RequestException)
from curl_cffi import requests as cffi

try:
    from zoneinfo import ZoneInfo
    BUDAPEST = ZoneInfo("Europe/Budapest")
except Exception:  # noqa: BLE001
    BUDAPEST = None

API_TMPL = "https://hirdetmenyek.gov.hu/api/hirdetmenyek/reszletezo/{id}"
BASE_URL = "https://hirdetmenyek.gov.hu"

# curl_cffi böngésző-profil (TLS-ujjlenyomat). Ha a szűrő később megváltozik és
# újra 403 jönne, próbáld: "chrome", "chrome131", "safari18_0".
IMPERSONATE = "safari18_0_ios"

REQUEST_DELAY = 1.0
REQUEST_JITTER = 0.5
REQUEST_TIMEOUT = 15
MAX_5XX_RETRY = 3

# A böngésző által küldött fejlécek — a bot-védelem ezt várja (a Referert
# kérésenként tesszük hozzá).
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "hu,en-US;q=0.9,en;q=0.8,de;q=0.7",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 "
        "Mobile/15E148 Safari/604.1"
    ),
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"iOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class RateLimitedError(Exception):
    """HTTP 403/429 — a futásnak azonnal le kell állnia."""


def build_session():
    """curl_cffi munkamenet böngésző TLS-ujjlenyomattal + 'bemelegítés':
    a főoldal letöltése beállítja a BIGip terheléselosztó-sütit, ahogy a
    böngészőben is történik."""
    s = cffi.Session(impersonate=IMPERSONATE)
    try:
        s.get(BASE_URL + "/", timeout=REQUEST_TIMEOUT)
    except Exception:  # noqa: BLE001 — a bemelegítés hibája nem végzetes
        pass
    return s


def polite_sleep() -> None:
    time.sleep(REQUEST_DELAY + random.uniform(0, REQUEST_JITTER))


def fetch_detail(session, ad_id: int):
    """Egy hirdetmény lekérése. None = nem létező ID (üres válasz / 404).
    403/429 → RateLimitedError; hálózati vagy 5xx hiba → requests.RequestException
    (hogy a hívók meglévő 'except requests.RequestException' ága elkapja)."""
    url = API_TMPL.format(id=ad_id)
    headers = {**HEADERS, "Referer": f"{BASE_URL}/reszletezo/{ad_id}"}
    last_err = None
    for attempt in range(MAX_5XX_RETRY):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        except Exception as e:  # noqa: BLE001 — curl_cffi hálózati hiba
            last_err = e
            time.sleep(2.0 * (attempt + 1))
            continue
        if resp.status_code in (403, 429):
            raise RateLimitedError(f"HTTP {resp.status_code} id={ad_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 500:
            last_err = requests.HTTPError(f"HTTP {resp.status_code} id={ad_id}")
            time.sleep(2.0 * (attempt + 1))
            continue
        body = (resp.text or "").strip()
        if not body:
            return None
        try:
            return resp.json()
        except ValueError:
            return None
    raise requests.RequestException(f"id={ad_id}: {last_err}")


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
