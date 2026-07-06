#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Egységtesztek az M0 normalizálóhoz — a 2026-07-01-i valós cache
(detail_api_cache.json) trükkös esetei + szintetikus szélsőesetek."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from normalize import _hu_number, normalize_item, parse_area_m2, parse_price

FIXTURE = Path(__file__).parent / "fixtures" / "detail_api_cache.json"


# --------------------------------------------------------------------------- #
# Számformátum
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("3.300.000", 3300000),
    ("795.400", 795400),
    ("40.000", 40000),
    ("2.600", 2600),
    ("1,0825", 1.0825),
    ("0,7680", 0.768),
    ("150.000", 150000),
    ("1 234 567", 1234567),
])
def test_hu_number(raw, expected):
    assert _hu_number(raw) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# Ár-osztályozás
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,kind,value", [
    ("3.300.000,-Ft", "total", 3300000),
    ("795.400 Ft", "total", 795400),
    ("40.000.- Ft", "total", 40000),
    ("150.000 Forint/ha/év", "per_ha_year", 150000),
    ("120.000 Ft/év", "total_per_year", 120000),
    ("2.600,- Ft/Ak/év", "per_ak_year", 2600),
    ("50 kg búza/AK/év", "in_kind", None),
    ("450.000 Ft/ha", "per_ha", 450000),
])
def test_parse_price(raw, kind, value):
    p = parse_price(raw)
    assert p["kind"] == kind
    if value is not None:
        assert p["value"] == pytest.approx(value)


# --------------------------------------------------------------------------- #
# Terület
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,m2", [
    ("1500 m2", 1500),
    ("119 m2", 119),
    ("854 m2; 0,60AK;", 854),
    ("1,0825 ha", 10825),
    ("0,7680Ha", 7680),
    ("28 ha 7758 m2; 20.86 AK;", 287758),          # a régi parser 280000-t adott
    ("1 ha 2050 m2; 28.32 AK;", 12050),
    ("21 ha 7976 m2-ből 7 ha 1411 m2", 71411),     # a régi parser 7976-ot adott
    ("21 ha7976 m2-ből 7 ha 1411 m2", 71411),      # szóköz nélküli változat
    ("5 ha", 50000),
    ("", None),
])
def test_parse_area(raw, m2):
    assert parse_area_m2(raw) == m2


# --------------------------------------------------------------------------- #
# Hirdetmény-szintű normalizálás
# --------------------------------------------------------------------------- #
def test_simple_sale():
    r = normalize_item(["795.400 Ft"], ["1500 m2"], "adás-vételére")
    assert r["ar_ft"] == 795400
    assert r["terulet_m2"] == 1500
    assert r["ft_per_ha"] == round(795400 / 0.15)
    assert r["comparable"] is True


def test_multi_parcel_sale():
    # a cache 2224994-es tétele: 3 földrészlet, mind teljes ár
    r = normalize_item(
        ["40.000.- Ft", "1.200.000.- Ft", "120.000.- Ft"],
        ["28 ha 7758 m2; 20.86 AK;", "1 ha 2050 m2; 28.32 AK;", "854 m2; 0,60AK;"],
        "adás-vételére")
    assert r["ar_ft"] == 1360000
    assert r["terulet_m2"] == 287758 + 12050 + 854
    assert r["comparable"] is True


def test_rent_per_ha_year():
    r = normalize_item(["150.000 Forint/ha/év"],
                       ["21 ha 7976 m2-ből 7 ha 1411 m2"], "haszonbérletére")
    assert r["ft_per_ha"] == 150000
    assert r["terulet_m2"] == 71411
    assert r["comparable"] is True


def test_rent_ak_based_not_comparable():
    r = normalize_item(["2.600,- Ft/Ak/év"], ["1,0825 ha"], "haszonbérletére")
    assert r["comparable"] is False
    assert r["status"] == "aranykorona_alapu_dij"
    assert r["terulet_m2"] == 10825    # a terület attól még megvan


def test_rent_in_kind_not_comparable():
    r = normalize_item(["50 kg búza/AK/év"], ["0,7680Ha"], "haszonbérletére")
    assert r["comparable"] is False
    assert r["status"] == "termeny_alapu_dij"


def test_mixed_units_not_comparable():
    r = normalize_item(["100.000 Ft", "5.000 Ft/ha/év"], ["1 ha", "2 ha"],
                       "adás-vételére")
    assert r["comparable"] is False
    assert r["status"] == "vegyes_egysegek"


def test_missing_area_no_ft_per_ha():
    r = normalize_item(["500.000 Ft"], [], "adás-vételére")
    assert r["ar_ft"] == 500000
    assert r["ft_per_ha"] is None
    assert r["comparable"] is False
    assert r["status"] == "terulet_hianyos"


def test_no_price():
    r = normalize_item([], ["1 ha"], "adás-vételére")
    assert r["status"] == "nincs_ar"


# --------------------------------------------------------------------------- #
# A teljes valós cache: minden tétel kap értéket VAGY indokolt státuszt
# --------------------------------------------------------------------------- #
def test_full_fixture_coverage():
    cache = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(cache) == 20
    ok, flagged = 0, 0
    for ad_id, row in cache.items():
        prices = [p.strip() for p in (row.get("Vételár") or "").split("|") if p.strip()]
        areas = [a.strip() for a in (row.get("Terület") or "").split("|") if a.strip()]
        altipus = (row.get("Típus (API)") or "").split("/")[-1].strip()
        r = normalize_item(prices, areas, altipus)
        if r["comparable"]:
            ok += 1
            assert r["ft_per_ha"] and r["ft_per_ha"] > 0, ad_id
        else:
            flagged += 1
            assert r["status"] != "ok", ad_id
    # a 20 valós tételből legalább 15-nek összehasonlíthatónak kell lennie
    assert ok >= 15, f"comparable: {ok}, flagged: {flagged}"
