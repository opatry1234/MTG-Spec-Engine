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

-- ── Access ─────────────────────────────────────────────────────────────────
-- The GitHub Action writes with the SERVICE key (bypasses RLS). If the engine
-- should read with the public ANON key, enable RLS + a read-only policy:
--   alter table card_prices_current  enable row level security;
--   alter table card_prices_history  enable row level security;
--   create policy read_current on card_prices_current for select using (true);
--   create policy read_history on card_prices_history for select using (true);
