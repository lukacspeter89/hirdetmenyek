#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
store.py — JSON-alapú adattár és állapotkezelés
-----------------------------------------------
Az adattár MAGA a cache: minden valaha lekért ID nyomot hagy
(items / seen), így egyetlen ID sem kérhető le kétszer.

Fájlok (a repo data/ könyvtárában):
  state.json            last_processed_id, backfill_floor_id, backfill_done
  items/YYYY-MM.json    Föld-hirdetmények, kifüggesztés hónapja szerint particionálva
  seen.json             nem tárolt ID-k oka: "empty" | "nonfold" | "parse_error"
  retry.json            üres választ adott friss ID-k újrapróbálási sora
  geo/telepulesek.json  település → koordináta/megye/járás cache
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.dir = Path(data_dir)
        self.state = _read_json(self.dir / "state.json", {})
        self.seen = _read_json(self.dir / "seen.json", {})
        self.retry = _read_json(self.dir / "retry.json", {})
        self._partitions = {}      # "YYYY-MM" -> {id: record}
        self._dirty = set()
        self._known_ids = None

    # ---------------- partíciók ---------------- #
    def _partition(self, month: str) -> dict:
        if month not in self._partitions:
            self._partitions[month] = _read_json(
                self.dir / "items" / f"{month}.json", {})
        return self._partitions[month]

    def _load_all_ids(self) -> set:
        if self._known_ids is None:
            ids = set()
            items_dir = self.dir / "items"
            if items_dir.exists():
                for f in items_dir.glob("*.json"):
                    ids.update(int(k) for k in _read_json(f, {}).keys())
            self._known_ids = ids
        return self._known_ids

    # ---------------- lekérdezés ---------------- #
    def is_known(self, ad_id: int) -> bool:
        """Igaz, ha az ID-t már valaha lekértük (adat, üres vagy nem-Föld)."""
        return (str(ad_id) in self.seen
                or ad_id in self._load_all_ids())

    # ---------------- írás ---------------- #
    def put_item(self, record: dict) -> None:
        month = (record.get("kifuggesztes") or "unknown")[:7]
        part = self._partition(month)
        part[str(record["id"])] = record
        self._dirty.add(month)
        self._load_all_ids().add(record["id"])
        self.retry.pop(str(record["id"]), None)

    def mark_seen(self, ad_id: int, reason: str) -> None:
        self.seen[str(ad_id)] = reason
        self.retry.pop(str(ad_id), None)

    def queue_retry(self, ad_id: int, now_iso: str, max_attempts: int = 4) -> None:
        e = self.retry.get(str(ad_id), {"first": now_iso, "attempts": 0})
        e["attempts"] += 1
        e["last"] = now_iso
        if e["attempts"] >= max_attempts:
            self.retry.pop(str(ad_id), None)
            self.mark_seen(ad_id, "empty")
        else:
            self.retry[str(ad_id)] = e

    def expire_retries(self, cutoff_iso: str) -> None:
        """48 óránál régebbi retry-bejegyzések véglegesítése."""
        for k in [k for k, v in self.retry.items() if v.get("first", "") < cutoff_iso]:
            self.retry.pop(k)
            self.seen[k] = "empty"

    # ---------------- mentés ---------------- #
    def save(self) -> None:
        for month in self._dirty:
            _write_json(self.dir / "items" / f"{month}.json",
                        self._partitions[month])
        self._dirty.clear()
        _write_json(self.dir / "seen.json", self.seen)
        _write_json(self.dir / "retry.json", self.retry)
        _write_json(self.dir / "state.json", self.state)

    # ---------------- statisztika ---------------- #
    def stats(self) -> dict:
        n_items = len(self._load_all_ids())
        return {
            "items": n_items,
            "seen_empty": sum(1 for v in self.seen.values() if v == "empty"),
            "seen_nonfold": sum(1 for v in self.seen.values() if v == "nonfold"),
            "retry_queue": len(self.retry),
            "last_processed_id": self.state.get("last_processed_id"),
            "backfill_floor_id": self.state.get("backfill_floor_id"),
            "backfill_done": self.state.get("backfill_done", False),
        }
