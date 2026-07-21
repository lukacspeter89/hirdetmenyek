#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
normalize.py — M0 normalizáló modul
-----------------------------------
A hirdetmenyek.gov.hu API nyers ár- és terület-stringjeit alakítja
összehasonlítható számokká (Ft, m², Ft/ha, Ft/ha/év).

Ismert egység-változatok (valós adatból):
  Ár:      "3.300.000,-Ft"          → teljes vételár (Ft)
           "795.400 Ft"             → teljes vételár
           "40.000.- Ft"            → teljes vételár
           "150.000 Forint/ha/év"   → bérleti díj Ft/ha/év
           "120.000 Ft/év"          → teljes éves bérleti díj
           "2.600,- Ft/Ak/év"       → aranykorona-alapú → NEM összehasonlítható
           "50 kg búza/AK/év"       → termény-alapú     → NEM összehasonlítható
  Terület: "1500 m2" | "119 m2"
           "1,0825 ha" | "0,7680Ha"
           "28 ha 7758 m2; 20.86 AK;"          → kombinált ha+m² (AK-t eldobjuk)
           "21 ha 7976 m2-ből 7 ha 1411 m2"    → részterület: a "-ből" UTÁNI rész
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- #
# Segéd
# --------------------------------------------------------------------------- #
def _clean(s: str) -> str:
    txt = str(s).replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", txt)


def _fold(s: str) -> str:
    """Kisbetűs, ékezet nélküli változat mintaillesztéshez."""
    nfkd = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _hu_number(raw: str, dot_is_decimal: bool = False):
    """Magyar számformátum → float.

    Vessző MINDIG tizedesjel (magyar konvenció: "1,0825").
    A pont alapból ezreselválasztó ("140.000 Ft"), DE a forrás néhol
    tizedespontot használ ("11.0483 ha", "273.45 Ak"). Ezért:
      - ha vessző ÉS pont is van, az utolsó elválasztó a tizedes;
      - dot_is_decimal=True esetén (hektár-kontextus) a pont tizedesjel;
      - egyébként a pont ezreselválasztó, KIVÉVE ha nem pontosan
        3 számjegy követi (pl. "11.0483" → tizedes).
    A ',-' / '.-' ártipográfiai farok nem tizedes."""
    txt = raw.strip().rstrip("-").rstrip(".,").strip()
    txt = txt.replace("\xa0", "").replace(" ", "")
    if not txt or not re.fullmatch(r"[\d.,]+", txt):
        return None

    has_c, has_d = "," in txt, "." in txt

    if has_c and has_d:
        # vegyes: az utolsóként előforduló elválasztó a tizedes
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif has_c:
        # magyarban a vessző tizedes; több vessző → ezres tagolás
        txt = txt.replace(",", ".") if txt.count(",") == 1 else txt.replace(",", "")
    elif has_d:
        frac = txt.rsplit(".", 1)[1]
        thousands = txt.count(".") > 1 or (len(frac) == 3 and not dot_is_decimal)
        if thousands:
            txt = txt.replace(".", "")

    try:
        return float(txt)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Ár
# --------------------------------------------------------------------------- #
# kind értékek: total | total_per_year | per_ha | per_ha_year |
#               per_ak_year | in_kind | unknown
_NUM_RE = re.compile(r"(\d[\d\s.,]*)")


def parse_price(raw: str) -> dict:
    """Egyetlen ár-string osztályozása és számmá alakítása."""
    out = {"raw": raw, "kind": "unknown", "value": None}
    if not raw:
        return out
    txt = _clean(raw)
    f = _fold(txt)

    # termény-alapú (kg búza, mázsa, tonna stb.)
    if re.search(r"\b(kg|mazsa|q|tonna|buza|kukorica|termeny)\b", f):
        out["kind"] = "in_kind"
        return out

    m = _NUM_RE.search(txt)
    if m:
        out["value"] = _hu_number(m.group(1))

    has_ft = re.search(r"\b(ft|forint|huf)\b", f) or "ft" in f
    per_ak = re.search(r"/\s*ak\b", f)
    per_ha = re.search(r"/\s*ha\b", f)
    per_ev = re.search(r"/\s*ev\b", f)

    if per_ak:
        out["kind"] = "per_ak_year"
    elif per_ha and per_ev:
        out["kind"] = "per_ha_year"
    elif per_ha:
        out["kind"] = "per_ha"
    elif per_ev:
        out["kind"] = "total_per_year"
    elif has_ft:
        out["kind"] = "total"
    else:
        out["kind"] = "unknown"

    if out["value"] is None:
        out["kind"] = "unknown"
    return out


# --------------------------------------------------------------------------- #
# Terület
# --------------------------------------------------------------------------- #
_COMBI_RE = re.compile(r"(\d[\d\s.,]*?)\s*ha\s*(\d[\d\s.,]*?)\s*m(?:2|²)\b")
_HA_RE = re.compile(r"(\d[\d\s.,]*?)\s*ha\b")
_M2_RE = re.compile(r"(\d[\d\s.,]*?)\s*m(?:2|²)\b")
_AK_SEG_RE = re.compile(r"\d[\d\s.,]*\s*ak\b[^;|]*", re.IGNORECASE)


def parse_area_m2(raw: str):
    """Terület-string → m² (int) vagy None."""
    if not raw:
        return None
    txt = _fold(_clean(raw))
    # részterület: "X-ből Y" → az eladott rész a "ből" UTÁN áll
    m = re.search(r"b[oe]l\b", txt)  # _fold után a 'ből' → 'bol'/'bel'
    if m:
        txt = txt[m.end():]
    # aranykorona-szegmensek eldobása, hogy a számaik ne zavarjanak
    txt = _AK_SEG_RE.sub(" ", txt)

    m = _COMBI_RE.search(txt)
    if m:
        ha = _hu_number(m.group(1), dot_is_decimal=True)
        m2 = _hu_number(m.group(2))
        if ha is not None and m2 is not None:
            return round(ha * 10000 + m2)

    m = _HA_RE.search(txt)
    if m:
        ha = _hu_number(m.group(1), dot_is_decimal=True)
        if ha is not None:
            return round(ha * 10000)

    m = _M2_RE.search(txt)
    if m:
        m2 = _hu_number(m.group(1))
        if m2 is not None:
            return round(m2)

    # csupasz szám: óvatosságból NEM tippelünk egységet
    return None


# --------------------------------------------------------------------------- #
# Hirdetmény-szintű normalizálás
# --------------------------------------------------------------------------- #
def normalize_item(prices: list, areas: list, altipus: str) -> dict:
    """Földrészletenkénti nyers listákból hirdetmény-szintű normalizált értékek.

    Visszatérés:
      terulet_m2      összterület (None, ha egyik sem parse-olható)
      ar_ft           teljes ár / éves díj Ft-ban (ha értelmezhető)
      ft_per_ha       adás-vétel: Ft/ha; haszonbérlet: Ft/ha/év
      comparable      bool — mehet-e a színskálára
      status          'ok' | magyarázó kód
    """
    out = {"terulet_m2": None, "ar_ft": None, "ft_per_ha": None,
           "comparable": False, "status": "ok"}

    parsed_a = [parse_area_m2(a) for a in (areas or [])]
    known_a = [a for a in parsed_a if a]
    if known_a:
        out["terulet_m2"] = sum(known_a)
    area_ha = out["terulet_m2"] / 10000.0 if out["terulet_m2"] else None
    area_complete = bool(known_a) and len(known_a) == len(areas or [])

    parsed_p = [parse_price(p) for p in (prices or [])]
    kinds = {p["kind"] for p in parsed_p}
    values = [p["value"] for p in parsed_p]

    if not parsed_p:
        out["status"] = "nincs_ar"
        return out
    if "in_kind" in kinds:
        out["status"] = "termeny_alapu_dij"
        return out
    if "per_ak_year" in kinds:
        out["status"] = "aranykorona_alapu_dij"
        return out
    if "unknown" in kinds or None in values:
        out["status"] = "ar_nem_ertelmezheto"
        return out
    if len(kinds) > 1:
        out["status"] = "vegyes_egysegek"
        return out

    kind = kinds.pop()
    is_rent = "haszonbér" in (altipus or "")

    if kind in ("total", "total_per_year"):
        out["ar_ft"] = round(sum(values))
        if area_ha and area_complete:
            out["ft_per_ha"] = round(out["ar_ft"] / area_ha)
            out["comparable"] = True
        else:
            out["status"] = "terulet_hianyos"
    elif kind in ("per_ha", "per_ha_year"):
        # fajlagos ár közvetlenül adott; több részletnél terület-súlyozott átlag
        if len(values) == 1:
            out["ft_per_ha"] = round(values[0])
        elif area_complete and all(parsed_a):
            w = sum(v * a for v, a in zip(values, parsed_a)) / sum(parsed_a)
            out["ft_per_ha"] = round(w)
        else:
            out["ft_per_ha"] = round(sum(values) / len(values))
        out["comparable"] = True
        if area_ha:
            out["ar_ft"] = round(out["ft_per_ha"] * area_ha)
    else:  # elvileg nem érhető el
        out["status"] = "ar_nem_ertelmezheto"
        return out

    # szemantikai őrszem: fajlagos éves díj adás-vételnél gyanús
    if kind == "per_ha_year" and not is_rent:
        out["status"] = "berleti_dij_adasvetelnel"
        out["comparable"] = False
    return out
