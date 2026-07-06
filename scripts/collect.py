#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect.py — napi gyűjtő (ID-bejáró, veszteségmentes)
-----------------------------------------------------
Folyamat:
  1. RSS letöltése → legmagasabb ID = cél ("jelzőcsengő").
     RSS-kiesésnél előre-szondázás: last_id-tól, amíg PROBE_STOP egymást
     követő üres válasz nem jön.
  2. last_processed_id+1 … cél: MINDEN ID lekérése a JSON API-ról.
     Már ismert ID-t (adat/üres/nem-Föld) SOHA nem kérünk újra.
  3. Föld-tétel → normalizálás → tár; nem-Föld → seen; üres → retry-sor.
  4. Retry-sor feldolgozása (max 4 próba / 48 óra).
  5. Új települések geokódolása (max 15/futás, Nominatim).

Védőkorlátok: futásonként max MAX_BATCH ID; 403/429-re azonnali leállás
(a részeredmény mentésre kerül); 3 egymást követő egyéb hiba → leállás.

Használat:  python scripts/collect.py [--max-batch N] [--dry-run]
Kilépési kód: 0 = rendben; 2 = rate-limit miatt megszakítva.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api_client  # noqa: E402
import geo as geomod  # noqa: E402
import rss  # noqa: E402
from api_client import RateLimitedError  # noqa: E402
from normalize import normalize_item  # noqa: E402
from store import Store  # noqa: E402

MAX_BATCH = 3000
MAX_CONSECUTIVE_ERRORS = 3
PROBE_STOP = 30          # RSS nélkül: ennyi egymást követő üres ID után állunk le
RETRY_WINDOW_H = 48

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("collect")


def process_id(session, st: Store, ad_id: int, now_iso: str) -> str:
    """Egy ID lekérése és besorolása. Visszatérés: 'item'|'nonfold'|'empty'."""
    data = api_client.fetch_detail(session, ad_id)
    if data is None:
        st.queue_retry(ad_id, now_iso)
        return "empty"
    record = api_client.parse_detail(ad_id, data)
    if record.get("kategoria") != "Föld":
        st.mark_seen(ad_id, "nonfold")
        return "nonfold"
    record.update(normalize_item(record["ar_raw"], record["terulet_raw"],
                                 record.get("altipus") or ""))
    st.put_item(record)
    return "item"


def run(max_batch: int = MAX_BATCH, dry_run: bool = False) -> int:
    st = Store()
    session = api_client.build_session()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    counts = {"item": 0, "nonfold": 0, "empty": 0, "skipped": 0}
    exit_code = 0

    rss_ids = rss.fetch_rss_ids(session)
    target = max(rss_ids) if rss_ids else None
    last = st.state.get("last_processed_id")

    if last is None:
        if target is None:
            log.error("Nincs állapot és az RSS sem elérhető — nem tudunk horgonyt tenni.")
            return 1
        last = min(rss_ids) - 1
        log.info("Első futás: horgony = %d (az RSS legrégebbi tétele elé).", last)
        st.state["backfill_floor_id"] = min(rss_ids)   # a backfill innen indul lefelé

    consecutive_err = 0
    consecutive_empty = 0
    processed = 0

    def walk_range():
        nonlocal last, consecutive_err, consecutive_empty, processed
        current = last
        while processed < max_batch:
            current += 1
            if target is not None and current > target:
                break
            if target is None and consecutive_empty >= PROBE_STOP:
                current -= 1
                break
            if st.is_known(current):
                counts["skipped"] += 1
                last = current
                continue
            try:
                kind = process_id(session, st, current, now_iso)
                counts[kind] += 1
                consecutive_err = 0
                consecutive_empty = consecutive_empty + 1 if kind == "empty" else 0
                processed += 1
                last = current
            except RateLimitedError as e:
                log.error("Rate limit: %s — azonnali leállás, mentés.", e)
                raise
            except (requests.RequestException, ValueError) as e:
                consecutive_err += 1
                log.error("Hiba id=%d: %s (%d/%d)", current, e,
                          consecutive_err, MAX_CONSECUTIVE_ERRORS)
                if consecutive_err >= MAX_CONSECUTIVE_ERRORS:
                    log.error("Túl sok egymást követő hiba — leállás.")
                    break
            api_client.polite_sleep()

    try:
        if target is not None and target - last > max_batch:
            log.warning("Nagy rés: %d ID (> %d). Csak %d-t dolgozunk fel, "
                        "a többit a következő futások érik utol.",
                        target - last, max_batch, max_batch)
        walk_range()

        # retry-sor: üres ID-k újrapróbálása
        cutoff = (now - timedelta(hours=RETRY_WINDOW_H)).isoformat(timespec="seconds")
        st.expire_retries(cutoff)
        for rid in [int(k) for k in list(st.retry.keys())][:200]:
            if processed >= max_batch:
                break
            try:
                kind = process_id(session, st, rid, now_iso)
                counts[kind] += 1
                processed += 1
            except RateLimitedError:
                exit_code = 2
                break
            except (requests.RequestException, ValueError):
                pass
            api_client.polite_sleep()

        # geokódolás
        telepulesek = set()
        for month in list(st._partitions.values()):
            telepulesek.update(r.get("telepules") for r in month.values())
        geo = geomod.load_geo()
        n_geo = geomod.geocode_missing({t for t in telepulesek if t}, geo)
        if n_geo and not dry_run:
            geomod.save_geo(geo)

    except RateLimitedError:
        exit_code = 2

    st.state["last_processed_id"] = last
    st.state["last_run"] = now_iso
    if not dry_run:
        st.save()
    log.info("Kész. Új tétel: %d | nem-Föld: %d | üres: %d | átugrott (ismert): %d",
             counts["item"], counts["nonfold"], counts["empty"], counts["skipped"])
    log.info("Állapot: %s", st.stats())
    return exit_code


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-batch", type=int, default=MAX_BATCH)
    ap.add_argument("--dry-run", action="store_true",
                    help="nem ír fájlt (próbafutáshoz)")
    args = ap.parse_args()
    raise SystemExit(run(args.max_batch, args.dry_run))
