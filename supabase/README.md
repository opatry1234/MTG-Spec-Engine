# Cloud price database — GitHub Actions + Supabase

A daily GitHub Action snapshots the **full** Scryfall price file into Supabase, so a
real point-in-time price history accumulates in the cloud with or without your
local machine. Adapted from your other project's pattern (Node→Python to match
this repo; normalized rows instead of a daily JSON blob so the engine can query
single cards and dates efficiently).

## Pieces

| File | Role |
|---|---|
| `supabase/schema.sql` | Two tables: `card_prices_current` (latest) + `card_prices_history` (change-only series) + a `price_at(card, date)` function |
| `ingest/snapshot_to_supabase.py` | Downloads Scryfall bulk, drops junk, writes to Supabase |
| `.github/workflows/daily-prices.yml` | Cron (06:00 UTC daily) that runs the script |

## One-time setup

1. **Supabase** → SQL editor → paste & run `supabase/schema.sql`.
2. **GitHub repo** → Settings → Secrets and variables → Actions → add:
   - `SUPABASE_URL` = `https://<project>.supabase.co`
   - `SUPABASE_SERVICE_KEY` = the **service_role** key (Project Settings → API). Keep secret.
3. Push the repo. Actions → "Daily MTG price snapshot" → **Run workflow** to test now;
   it then runs daily on its own.

## What gets stored / skipped

Kept: every real, paper, spec-able card (cheapest printing's USD + foil).
Skipped (never spec targets): basic lands, tokens, emblems, stickers, attractions,
dungeons, art-series/memorabilia, non-paper, and a short ubiquitous-staple list
(Sol Ring, Arcane Signet, Command Tower, Commander's Sphere, signet-tier rocks…).
Edit `JUNK_*` / `UBIQUITOUS_STAPLES` in the script to tune.

**Storage:** history rows are written only when a price *changes*, so the free tier
(~500MB) lasts: ~30k cards seed once, then only the daily movers. Point-in-time
price = the most recent history row on/before the anchor date (`price_at()`).

## Supply + seller concentration (schema ready, data pending)

`available_copies`, `seller_count`, and `copies_per_seller` are columns already in
the schema but stay NULL — there is still no free historical listings/seller feed
(TCGplayer's API is closed to new keys). When you wire one in (scrape or paid),
populate those columns in the same daily job. **Seller concentration is a real spike
signal**: 100 copies across 5 sellers (copies_per_seller = 20) is far more spike-prone
than 100 copies across 100 sellers (= 1), because a few sellers can lift the floor.
The engine will fold `copies_per_seller` into effective scarcity once data exists.

## Engine reference (next step)

The engine reads point-in-time price from Supabase instead of a local table:
`SELECT price_at('<card>', '<anchor_date>')`, or bulk-pull `card_prices_history`.
I'll wire `point_in_time_price()` to this once the first snapshot lands.
