-- MTG Spec Engine — price/supply snapshot schema (Supabase / Postgres)
-- Run once in the Supabase SQL editor.
--
-- Design: insert-on-change history so we keep a full point-in-time series WITHOUT
-- storing 30k rows/day forever (the free tier is ~500MB). `*_current` always holds
-- the latest value; `*_history` gets a row only when a value changes. Point-in-time
-- price at an anchor = the most recent history row on/before that date.

-- ── Latest snapshot (one row per card, upserted every run) ──────────────────
create table if not exists card_prices_current (
    card_name        text primary key,
    oracle_id        text,             -- Scryfall oracle_id UUID (stable card identity, not rules text)
    price_usd        numeric,          -- cheapest paper printing, USD
    price_usd_foil   numeric,
    -- supply / seller signals (nullable until a TCGplayer-style feed exists):
    available_copies integer,          -- total copies listed for sale
    seller_count     integer,          -- distinct sellers
    copies_per_seller numeric,         -- available_copies / seller_count (concentration)
    updated_at       timestamptz default now()
);

-- ── Change-only history (point-in-time source) ─────────────────────────────
create table if not exists card_prices_history (
    id               bigint generated always as identity primary key,
    card_name        text not null,
    snapshot_date    date not null,
    price_usd        numeric,
    price_usd_foil   numeric,
    available_copies integer,
    seller_count     integer,
    copies_per_seller numeric,
    -- sales volume from the TCGplayer chart endpoint (quantity actually sold):
    quantity_sold    integer,
    transaction_count integer,
    market_price     numeric,        -- TCGplayer market price (native, vs Scryfall usd)
    unique (card_name, snapshot_date)
);

create index if not exists idx_cph_card_date
    on card_prices_history (card_name, snapshot_date desc);

-- ── Point-in-time helper: latest known price on/before a date ───────────────
create or replace function price_at(p_card text, p_date date)
returns numeric language sql stable as $$
    select price_usd
    from card_prices_history
    where card_name = p_card and snapshot_date <= p_date
    order by snapshot_date desc
    limit 1
$$;

-- ── Pinned volume source printing (one per card) ───────────────────────────
-- Which TCGplayer product the volume scraper uses for a card. Pinned once (the
-- cheapest STANDARD-frame, non-promo printing — that's where ~80% of spec-range
-- spikes happen) so the weekly series can't drift between printings as prices move.
create table if not exists volume_card_source (
    card_name      text primary key,
    tcgplayer_id   integer not null,
    set_code       text,
    variant        text,            -- 'standard' | 'extended art' | 'borderless' | ...
    usd            numeric,         -- price at pin time (audit only)
    pinned_at      timestamptz default now()
);

-- ── Coverage sanity check ──────────────────────────────────────────────────
-- One-row summary of how much of the card pool has volume data captured.
-- Run this file once, then in the dashboard: Table Editor → Views → volume_coverage,
-- or SQL editor: `select * from volume_coverage;`
create or replace view volume_coverage as
select
    (select count(distinct card_name) from card_prices_history
        where quantity_sold is not null)                              as cards_with_volume,
    (select count(*) from card_prices_current)                        as price_pool,
    round(100.0 * (select count(distinct card_name) from card_prices_history
                       where quantity_sold is not null)
          / nullif((select count(*) from card_prices_current), 0), 2) as pct_pool_covered,
    (select count(*) from card_prices_history
        where quantity_sold is not null)                              as volume_rows,
    (select min(snapshot_date) from card_prices_history
        where quantity_sold is not null)                              as earliest_volume_date,
    (select max(snapshot_date) from card_prices_history
        where quantity_sold is not null)                              as latest_volume_date,
    now()                                                             as checked_at;

-- Per-card drill-down: weeks captured + freshness (stalest first).
create or replace view volume_coverage_by_card as
select
    card_name,
    count(*)            as weeks_captured,
    min(snapshot_date)  as first_date,
    max(snapshot_date)  as last_date,
    sum(quantity_sold)  as total_units
from card_prices_history
where quantity_sold is not null
group by card_name
order by last_date asc, weeks_captured asc;

-- ── Migrating an EXISTING database (already created before volume columns) ──
-- Run these once if card_prices_history predates the volume columns:
--   alter table card_prices_history add column if not exists quantity_sold integer;
--   alter table card_prices_history add column if not exists transaction_count integer;
--   alter table card_prices_history add column if not exists market_price numeric;

-- ── Access ─────────────────────────────────────────────────────────────────
-- The GitHub Action writes with the SERVICE key (bypasses RLS). If the engine
-- should read with the public ANON key, enable RLS + a read-only policy:
--   alter table card_prices_current  enable row level security;
--   alter table card_prices_history  enable row level security;
--   create policy read_current on card_prices_current for select using (true);
--   create policy read_history on card_prices_history for select using (true);
