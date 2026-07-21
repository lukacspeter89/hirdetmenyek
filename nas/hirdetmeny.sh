#!/bin/bash
# hirdetmeny.sh — NAS-oldali gyűjtő "agy" (Synology DS214play, lakossági IP).
# A letöltést sima `curl` végzi (az API böngésző-TLS nélkül is tiszta JSON-t ad),
# így nincs szükség architektúra-specifikus Go binárisra. Ez a szkript dönti el,
# mely ID-ket kérje le, és a nyers JSON-t a repo raw/ könyvtárába menti.
# A normalizálást/geokódolást/térképépítést a GitHub Actions végzi a raw/-ból.
#
# Használat:
#   hirdetmeny.sh collect     # előre-szondázás (napi gyűjtés)
#   hirdetmeny.sh backfill    # visszamenőleges töltés (éjszaka)
#
# Beállítás: állítsd a REPO_DIR útvonalat a NAS-odon.
set -euo pipefail
# A DSM Feladatütemező minimális PATH-tal indít — a git/curl megtalálásához bővítjük.
export PATH="$PATH:/usr/bin:/bin:/usr/local/bin:/opt/bin:/usr/syno/bin"
# ------------------------- BEÁLLÍTÁSOK ------------------------- #
REPO_DIR="${REPO_DIR:-/volume1/hirdetmenyek}"       # a klónozott repo a NAS-on
BASE="https://hirdetmenyek.gov.hu"
API="$BASE/api/hirdetmenyek/reszletezo"
DELAY="${DELAY:-1.3}"          # másodperc két kérés között (szerverkímélet)
MAX_BATCH="${MAX_BATCH:-1500}" # max kérés futásonként
PROBE_STOP="${PROBE_STOP:-30}" # ennyi egymást követő "üres" ID után állunk (előre)
RECHECK="${RECHECK:-40}"       # ennyi korábbi ID-t újra megnézünk (késői publikálás)
BACKFILL_CHUNK="${BACKFILL_CHUNK:-1500}"   # backfill: ennyi ID/futás lefelé
BACKFILL_SPAN="${BACKFILL_SPAN:-9000}"     # kb. 60 nap ID-ben; eddig megyünk vissza
CURL_TIMEOUT="${CURL_TIMEOUT:-30}"         # egy kérés max ideje (mp)
RAW="$REPO_DIR/raw"
ITEMS="$RAW/items"
STATE="$RAW/state"
LOG() { echo "$(date -u +%H:%M:%S) [$1] ${2:-}"; }
# ------------------------- ÁLLAPOT ------------------------- #
LAST_PROCESSED=0; BACKFILL_FLOOR=0; BACKFILL_DONE=0
load_state() {
  mkdir -p "$ITEMS"
  [ -f "$STATE" ] && . "$STATE" || true
}
save_state() {
  {
    echo "LAST_PROCESSED=$LAST_PROCESSED"
    echo "BACKFILL_FLOOR=$BACKFILL_FLOOR"
    echo "BACKFILL_DONE=$BACKFILL_DONE"
  } > "$STATE"
}
# ------------------------- LETÖLTÉS ------------------------- #
# fetch_one <id>  → beállítja: F_STATUS, F_KIND ("fold"|"other"|"empty"|"forbidden"|"ratelimit")
# és Föld esetén elmenti a nyers JSON-t raw/items/<id>.json-ba.
fetch_one() {
  local id="$1" status body tmp
  tmp="$(mktemp)"
  # -o: törzs fájlba (bájthű), -w: HTTP státusz a stdoutra; hálózati hiba → "000".
  status="$(curl -sS --connect-timeout 10 --max-time "$CURL_TIMEOUT" \
      -H "Accept: application/json" \
      -o "$tmp" -w '%{http_code}' "$API/$id" 2>/dev/null || echo "000")"
  body="$(cat "$tmp")"
  rm -f "$tmp"
  F_STATUS="$status"
  if [ "$status" = "429" ] || [ "$status" = "000" ]; then
    F_KIND="ratelimit"; return 0
  fi
  if [ "$status" = "403" ]; then
    F_KIND="forbidden"; return 0
  fi
  if [ "$status" != "200" ] || [ -z "${body//[$'\t\r\n ']/}" ]; then
    F_KIND="empty"; return 0
  fi
  # Van tartalom: Föld-e?
  if printf '%s' "$body" | grep -q '"kategoria":"Föld"'; then
    printf '%s' "$body" > "$ITEMS/$id.json"
    F_KIND="fold"
  else
    F_KIND="other"
  fi
}
known() { [ -f "$ITEMS/$1.json" ]; }
# ------------------------- MÓDOK ------------------------- #
run_collect() {
  local start id processed=0 empties=0 forb=0 highest="$LAST_PROCESSED"
  start=$(( LAST_PROCESSED - RECHECK )); [ "$start" -lt 1 ] && start=1
  id="$start"
  LOG INFO "collect indul: $start-től, utolsó feldolgozott=$LAST_PROCESSED"
  while [ "$processed" -lt "$MAX_BATCH" ]; do
    if known "$id"; then id=$(( id + 1 )); continue; fi
    fetch_one "$id"
    if [ "$F_KIND" = "ratelimit" ]; then
      LOG WARN "429/hálózati hiba az id=$id-nél — leállás, mentés."; break
    fi
    processed=$(( processed + 1 ))
    if [ "$F_KIND" = "forbidden" ]; then
      forb=$(( forb + 1 ))
      if [ "$forb" -ge 15 ]; then
        LOG WARN "15 egymást követő 403 — valószínű blokk, leállás."; break
      fi
      LOG INFO "403 az id=$id-nél — átugorva."
      empties=$(( empties + 1 ))
      if [ "$id" -gt "$LAST_PROCESSED" ] && [ "$empties" -ge "$PROBE_STOP" ]; then
        LOG INFO "$PROBE_STOP egymást követő üres — elértük a legfrissebbet."; break
      fi
      id=$(( id + 1 )); sleep "$DELAY"; continue
    fi
    forb=0
    if [ "$F_KIND" = "empty" ]; then
      empties=$(( empties + 1 ))
      if [ "$id" -gt "$LAST_PROCESSED" ] && [ "$empties" -ge "$PROBE_STOP" ]; then
        LOG INFO "$PROBE_STOP egymást követő üres — elértük a legfrissebbet."; break
      fi
    else
      empties=0; highest="$id"
      [ "$F_KIND" = "fold" ] && LOG INFO "Föld mentve: id=$id"
    fi
    id=$(( id + 1 ))
    sleep "$DELAY"
  done
  [ "$highest" -gt "$LAST_PROCESSED" ] && LAST_PROCESSED="$highest"
  [ "$BACKFILL_FLOOR" -eq 0 ] && BACKFILL_FLOOR="$LAST_PROCESSED"
  LOG INFO "collect kész. feldolgozott=$processed, last_processed=$LAST_PROCESSED"
}
run_backfill() {
  local id processed=0 forb=0 floor_min
  if [ "$BACKFILL_DONE" = "1" ] || [ "$BACKFILL_FLOOR" -le 1 ]; then
    LOG INFO "backfill kész/nincs teendő."; return 0
  fi
  floor_min=$(( LAST_PROCESSED - BACKFILL_SPAN )); [ "$floor_min" -lt 1 ] && floor_min=1
  id=$(( BACKFILL_FLOOR - 1 ))
  LOG INFO "backfill indul: $id-től lefelé, cél floor=$floor_min"
  while [ "$processed" -lt "$BACKFILL_CHUNK" ] && [ "$id" -gt "$floor_min" ]; do
    if known "$id"; then BACKFILL_FLOOR="$id"; id=$(( id - 1 )); continue; fi
    fetch_one "$id"
    if [ "$F_KIND" = "ratelimit" ]; then
      LOG WARN "429/hálózati hiba az id=$id-nél — leállás, mentés."; break
    fi
    processed=$(( processed + 1 ))
    if [ "$F_KIND" = "forbidden" ]; then
      forb=$(( forb + 1 ))
      if [ "$forb" -ge 15 ]; then
        LOG WARN "15 egymást követő 403 — valószínű blokk, leállás."; break
      fi
      LOG INFO "403 az id=$id-nél — átugorva."
    else
      forb=0
    fi
    [ "$F_KIND" = "fold" ] && LOG INFO "Föld mentve (backfill): id=$id"
    BACKFILL_FLOOR="$id"
    id=$(( id - 1 ))
    sleep "$DELAY"
  done
  [ "$BACKFILL_FLOOR" -le "$floor_min" ] && { BACKFILL_DONE=1; LOG INFO "backfill elérte a 60 napos határt — KÉSZ."; }
  LOG INFO "backfill kész. feldolgozott=$processed, floor=$BACKFILL_FLOOR"
}
# ------------------------- FŐ ------------------------- #
main() {
  local mode="${1:-collect}"
  cd "$REPO_DIR"
  git pull --rebase --quiet origin main || LOG WARN "git pull sikertelen (folytatjuk)"
  load_state
  case "$mode" in
    collect)  run_collect ;;
    backfill) run_backfill ;;
    *) echo "ismeretlen mód: $mode (collect|backfill)"; exit 2 ;;
  esac
  save_state
  git add raw
  if git diff --cached --quiet; then
    LOG INFO "Nincs változás."
  else
    git commit -q -m "raw: $mode $(date -u +'%Y-%m-%d %H:%M') UTC"
    git pull --rebase --quiet origin main || true
    git push -q origin main && LOG INFO "Feltöltve."
  fi
}
main "$@"
