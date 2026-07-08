# Hirdetmény-térkép

Földhivatali hirdetmények (adás-vétel + haszonbérlet) automatikus gyűjtése a
`hirdetmenyek.gov.hu` oldalról, és interaktív térkép (GitHub Pages): kártyás
kereső, szűrők, járáson belül normalizált zöld→piros Ft/ha színskála.

## Architektúra

A gov.hu-t **lakossági IP-ről** kell lekérni, ezért a gyűjtés a NAS-on fut, a
feldolgozás és a térkép pedig a GitHub-on:

```
  NAS (Synology, lakossági IP)                  GitHub
  ────────────────────────────                  ──────────────────────────
  nas/hirdetmeny.sh  (bash + curl)              .github/workflows/process.yml
        │  ID-bejárás a JSON API-n                     │  push: raw/** trigger
        │  Föld-tétel → raw/items/<id>.json            ▼
        ▼                                        scripts/process.py
  git commit + push  ───────────────────►        (normalizál + geokódol)
        raw/state, raw/items/                            │
                                                         ▼
                                                   data/  (térkép-adat)
                                                         │
                                                         ▼
                                                   index.html  (Leaflet, Pages)
```

- **ID-bejárás, nem RSS-ablak.** A gyűjtő a legutóbb feldolgozott ID-tól
  fölfelé MINDEN ID-t lekér a nyilvános JSON API-ról
  (`/api/hirdetmenyek/reszletezo/{id}`), így két futás között sincs
  adatvesztés.
- **Az adattár maga a cache.** A NAS a `raw/items/`-ben tárolja a Föld-tételek
  nyers JSON-ját; a `raw/state` a kurzort (`LAST_PROCESSED`,
  `BACKFILL_FLOOR`, `BACKFILL_DONE`). Ugyanaz az ID nem kérdeződik le kétszer.
- **Szerverkímélet.** Egyetlen szál, 1,5–3 mp/kérés, 403/429-re azonnali
  leállás; a backfill csak éjszaka fut.
- **A felhő-IP nem hívja a gov.hu-t.** Az Actions csak a `raw/`-ból épít
  `data/`-t és Nominatimról (OSM) geokódol — a gov.hu bot-védelme itt nem gond.

## Fájlok

| Útvonal | Mi ez |
|---|---|
| `nas/hirdetmeny.sh` | NAS-oldali gyűjtő (curl): `collect` és `backfill` mód |
| `.github/workflows/process.yml` | `raw/**` push → `data/` építése |
| `scripts/process.py` | a nyers JSON-ból térkép-adatot épít (normalizál + geokódol) |
| `scripts/api_client.py` | a részletező JSON → egységes rekord (`parse_detail`) |
| `scripts/normalize.py` | ár/terület egység-felismerő normalizálás |
| `scripts/geo.py` | település → koordináta/megye/járás (Nominatim, cache-elt) |
| `scripts/store.py` | particionált JSON-tár és index a térképnek |
| `raw/items/<id>.json` | a NAS által letöltött nyers Föld-hirdetmények |
| `raw/state` | NAS gyűjtő-kurzor (ID-horgony, backfill-állapot) |
| `data/items/YYYY-MM.json` | feldolgozott hirdetmények, kifüggesztés hónapja szerint |
| `data/geo/` | `jaras_static.json` (település→járás, KSH) + `telepulesek.json` (koordináta-cache) |
| `index.html` | önálló, egyfájlos Leaflet-térkép; közvetlenül a `data/`-t olvassa |
| `tests/` | egységtesztek a normalizálóhoz |

## Beüzemelés

### 1. NAS (gyűjtés)

A repo klónja a NAS-on, majd a DSM Feladatütemezőben `lukacspeter`
felhasználóként (hogy a `~/.ssh` deploy key-t és a `~/.gitconfig`-ot használja):

```bash
# napi gyűjtés — 12 óránként (pl. 06:00 és 18:00)
DELAY=2.5 REPO_DIR=/volume1/homes/lukacspeter/hirdetmenyek \
  /volume1/homes/lukacspeter/hirdetmenyek/nas/hirdetmeny.sh collect

# 60 napos visszatöltés — éjszaka (pl. 02:00), amíg magától be nem fejezi
DELAY=3 REPO_DIR=/volume1/homes/lukacspeter/hirdetmenyek \
  /volume1/homes/lukacspeter/hirdetmenyek/nas/hirdetmeny.sh backfill
```

A `collect` és a `backfill` ne fusson egyszerre (mindkettő ugyanabba a repóba
pushol). A GitHub-hitelesítés SSH deploy key-jel megy (Settings → Deploy keys,
**Allow write access**).

### 2. GitHub (feldolgozás + térkép)

- *Settings → Actions → General → Workflow permissions*: **Read and write**.
- A `process.yml` magától fut minden `raw/**` push után; kézzel is indítható
  (*Actions → Feldolgozás → Run workflow*).
- *Settings → Pages → Source: Deploy from a branch → `main` / `(root)`*.
  Cím: `https://lukacspeter89.github.io/hirdetmenyek/`

## Rekord-formátum (kivonat)

```json
{
  "id": 2225815,
  "tipus": "adasvetel",
  "telepules": "Lepsény",
  "hrsz": ["1698"],
  "muvelesi_ag": ["rét és gazdasági épület"],
  "ar_raw": ["3.300.000,-Ft"],
  "terulet_raw": ["119 m2"],
  "ar_ft": 3300000,
  "terulet_m2": 119,
  "ft_per_ha": 277310924,
  "comparable": true,
  "status": "ok",
  "kifuggesztes": "2026-07-06",
  "lejarat": "2026-08-05",
  "link": "https://hirdetmenyek.gov.hu/reszletezo/2225815"
}
```

`comparable: false` esetén a `status` mondja meg, miért nem kerül a tétel a
Ft/ha színskálára: `aranykorona_alapu_dij`, `termeny_alapu_dij`,
`vegyes_egysegek`, `terulet_hianyos`, `ar_nem_ertelmezheto`, `nincs_ar`.

## Tesztek

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```
