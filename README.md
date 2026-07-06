# Hirdetmény-térkép — adatgyűjtő (M0 + M1)

Földhivatali hirdetmények (adás-vétel + haszonbérlet) automatikus gyűjtése a
hirdetmenyek.gov.hu oldalról, GitHub Actions-szel. Ez a repo a térkép-projekt
adat-oldala; a térkép (GitHub Pages) az M2 mérföldkőben épül rá.

## Hogyan működik

- **ID-bejárás, nem RSS-ablak.** Az RSS csak "jelzőcsengő": a legfrissebb
  hirdetmény-ID-t adja. A gyűjtő a legutóbb feldolgozott ID-tól odáig MINDEN
  ID-t lekér a nyilvános JSON API-ról
  (`/api/hirdetmenyek/reszletezo/{id}`), így akkor sincs adatvesztés, ha két
  futás között több száz hirdetmény érkezik.
- **Az adattár maga a cache.** Minden valaha lekért ID nyomot hagy (adat,
  "üres" vagy "nem Föld" jelölés) — ugyanaz az ID soha nem kérdeződik le
  kétszer, se az élő gyűjtőben, se a backfillben.
- **Szerverkímélet.** Egyetlen szál, 1-1,5 mp + jitter kérésenként,
  403/429-re azonnali leállás (nem lassítva próbálkozik tovább), backfill
  csak éjszaka.

## Beüzemelés

1. Hozz létre egy **publikus** GitHub repót (publikusnál az Actions ingyen,
   perckorlát nélkül fut), és pushold fel ezt a könyvtárat.
2. A repo *Settings → Actions → General → Workflow permissions* alatt add meg
   a **Read and write permissions**-t.
3. Indítsd el kézzel egyszer a **Gyűjtés** workflow-t (*Actions →
   Gyűjtés (ID-bejáró) → Run workflow*). Az első futás leteszi a horgonyt
   (`last_processed_id`, `backfill_floor_id`).
4. A **Backfill** ezután éjszakánként magától fut, és ~6-8 éjszaka alatt
   visszatölti az elmúlt 60 napot, majd kikapcsolja magát
   (`backfill_done: true`).

## Fájlok

| Útvonal | Mi ez |
|---|---|
| `scripts/collect.py` | napi gyűjtő (ID-bejáró, retry-sor, geokódolás) |
| `scripts/backfill.py` | egyszeri 60 napos visszatöltés, éjszakai darabokban |
| `scripts/api_client.py` | gov.hu JSON API kliens + rekord-parser |
| `scripts/normalize.py` | M0: ár/terület egység-felismerő normalizálás |
| `scripts/rss.py` | RSS "jelzőcsengő" (csak ID-k) |
| `scripts/store.py` | particionált JSON-tár, állapot, dedup |
| `scripts/geo.py` | település → koordináta/megye/járás (Nominatim, cache-elt) |
| `data/items/YYYY-MM.json` | hirdetmények, kifüggesztés hónapja szerint |
| `data/state.json` | horgony-ID-k, backfill-állapot |
| `tests/` | egységtesztek (36 db) a valós 2026-07-01-i mintán |

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

`comparable: false` esetén a `status` mondja meg, miért nem mehet a tétel a
Ft/ha színskálára: `aranykorona_alapu_dij`, `termeny_alapu_dij`,
`vegyes_egysegek`, `terulet_hianyos`, `ar_nem_ertelmezheto`, `nincs_ar`.

## Tesztek futtatása

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```
