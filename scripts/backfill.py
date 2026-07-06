#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill.py — egyszeri visszamenőleges gyűjtés (60 nap)
-------------------------------------------------------
A napi gyűjtő horgony-ID-jától LEFELÉ járja be az ID-ket éjszakai
darabokban. A napi gyűjtő felfelé megy, ez lefelé — a két tartomány
diszjunkt, és az adattár-cache miatt ugyanaz az ID kétszer sosem
kérdeződik le.

Leállási feltétel: STOP_STREAK egymást követő Föld-tétel, amelynek
kifüggesztése régebbi, mint (ma − BACKFILL_DAYS nap) → backfill_done=True,
a workflow ezután már nem csinál semmit.

Szerverkímélet: azonos az élő gyűjtővel (1-1,5 mp/kérés, egyetlen szál,
403/429-re azonnali leállás); lefelé az üres ID végleges "empty" jelölést
kap (régi tartományban utólagos publikálás nem várható).

Használat:  python scripts/backfill.py [--max-batch N]
Kilépési kód: 0 = rendben / kész; 2 = rate-limit miatt megszakítva.
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
from api_client import RateLimitedError  # noqa: E402
from normalize import normalize_item  # noqa: E402
from store import Store  # noqa: E402

BACKFILL_DAYS = 60
MAX_BATCH = 1800          # ~40-50 perc futás 1-1,5 mp/kérés mellett
STOP_STREAK = 50
MAX_CONSECUTIVE_ERRORS = 3

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backfill")


def run(max_batch: int = MAX_BATCH) -> int:
    st = Store()
    if st.state.get("backfill_done"):
        log.info("A backfill már kész — nincs teendő.")
        return 0
    floor = st.state.get("backfill_floor_id")
    if floor is None:
        log.error("Nincs backfill_floor_id — előbb a collect.py-nak kell "
                  "legalább egyszer lefutnia (az teszi le a horgonyt).")
        return 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)) \
        .date().isoformat()
    session = api_client.build_session()
    counts = {"item": 0, "nonfold": 0, "empty": 0, "skipped": 0}
    old_streak = 0
    consecutive_err = 0
    processed = 0
    current = floor
    exit_code = 0

    try:
        while processed < max_batch and current > 1:
            current -= 1
            if st.is_known(current):
                counts["skipped"] += 1
                floor = current
                continue
            try:
                data = api_client.fetch_detail(session, current)
                if data is None:
                    st.mark_seen(current, "empty")   # lefelé: végleges
                    counts["empty"] += 1
                else:
                    record = api_client.parse_detail(current, data)
                    if record.get("kategoria") != "Föld":
                        st.mark_seen(current, "nonfold")
                        counts["nonfold"] += 1
                    else:
                        record.update(normalize_item(
                            record["ar_raw"], record["terulet_raw"],
                            record.get("altipus") or ""))
                        st.put_item(record)
                        counts["item"] += 1
                        kif = record.get("kifuggesztes")
                        old_streak = old_streak + 1 if (kif and kif < cutoff) else 0
                        if old_streak >= STOP_STREAK:
                            st.state["backfill_done"] = True
                            log.info("%d egymást követő %s előtti tétel — "
                                     "a backfill KÉSZ.", STOP_STREAK, cutoff)
                            floor = current
                            break
                consecutive_err = 0
                processed += 1
                floor = current
            except RateLimitedError as e:
                log.error("Rate limit: %s — azonnali leállás, mentés.", e)
                exit_code = 2
                break
            except (requests.RequestException, ValueError) as e:
                consecutive_err += 1
                log.error("Hiba id=%d: %s (%d/%d)", current, e,
                          consecutive_err, MAX_CONSECUTIVE_ERRORS)
                if consecutive_err >= MAX_CONSECUTIVE_ERRORS:
                    log.error("Túl sok egymást követő hiba — leállás.")
                    break
            api_client.polite_sleep()
    finally:
        st.state["backfill_floor_id"] = floor
        st.save()

    log.info("Futás vége. Új tétel: %d | nem-Föld: %d | üres: %d | átugrott: %d",
             counts["item"], counts["nonfold"], counts["empty"], counts["skipped"])
    log.info("Állapot: %s", st.stats())
    return exit_code


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-batch", type=int, default=MAX_BATCH)
    args = ap.parse_args()
    raise SystemExit(run(args.max_batch))
