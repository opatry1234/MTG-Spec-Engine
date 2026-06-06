"""
Configuration and constants for the MTG Spec Engine.

Load API keys and configuration from environment variables.
"""

import os
from datetime import date
from pathlib import Path

# Load a local .env (gitignored) so SUPABASE_URL / SUPABASE_KEY etc. persist
# without re-exporting each shell session. Safe no-op if python-dotenv is absent.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Paths
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATABASE_URL = f"sqlite:///{BASE_DIR / 'mtg_spec.db'}"
# Long timeout helps batch rebuild scripts when the DB is open elsewhere (e.g. Streamlit).
DATABASE_CONNECT_ARGS = {"check_same_thread": False, "timeout": 60}

# API Keys
SCRYFALL_API_BASE = "https://api.scryfall.com"
TCGAPIS_BASE = "https://tcgapis.com/api/v2"
TCGAPIS_KEY = os.getenv("TCGAPIS_KEY", "")

# Supabase cloud price database (read side for the engine). The daily GitHub
# Action writes prices; the engine syncs card_prices_current into a local cache.
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_SERVICE_KEY", "")

# Spec price gate / ignition.
PRICE_GATE_USD = 10.0          # drop live-priced candidates over this (cheap-only specs)
PRICE_LIVE_GRACE_DAYS = 21     # treat cached price as point-in-time only within this window of the anchor

# Feature Engineering
AVERAGE_LANDS_PER_PRECON = 37
ETERNAL_STAPLES_COUNT = 8

# Deck skeleton stratification
PRODUCT_TYPE_ENUM = [
    "standard_set_commander",
    "universes_beyond",
    "commander_masters",
    "annual_commander_product",
    "commander_legends",
    "other",
]

RELEASE_ERAS = [
    ("2015-2018", date(2015, 1, 1), date(2018, 12, 31)),
    ("2019-2022", date(2019, 1, 1), date(2022, 12, 31)),
    ("2023-present", date(2023, 1, 1), None),
]

PRODUCT_TYPES_YAML = DATA_DIR / "metadata" / "product_types.yaml"
SKELETON_DISTRIBUTIONS_PATH = DATA_DIR / "analytics" / "skeleton_distributions.json"

# ML Model Parameters
XGB_PARAMS = {
    "n_estimators": 500,
    "max_depth": 6,
    "learning_rate": 0.05,
    "scale_pos_weight": 49,
    "eval_metric": "auc",
}

# Rate limiting
SCRYFALL_RATE_LIMIT_DELAY = 0.1  # seconds between API calls

# Price spike detection (anchored to decklist reveal date)
SPIKE_BASELINE_START_DAYS = -28
SPIKE_BASELINE_END_DAYS = -7
SPIKE_PEAK_START_DAYS = 0
SPIKE_PEAK_END_DAYS = 120
SPIKE_MIN_RELATIVE_PCT = 0.25
SPIKE_MIN_ABSOLUTE_USD = 0.50
GOLDEN_SPIKE_MIN_RELATIVE_PCT = 1.0

# Spec model feature weights (weighted linear score; surprising omission is headline)
SPEC_FEATURE_WEIGHTS = {
    # Phase-3 headline: card should have been in the deck but wasn't
    "surprising_omission_score": 13.0,
    "deck_synergy_direct": 9.0,
    "oracle_text_overlap": 8.0,
    "mechanical_pool_size": 7.0,
    "tfidf_similarity": 6.0,
    "creature_type_overlap": 5.0,
    "keyword_overlap_score": 5.0,
    # Market supply (replaces num_printings as a ranking signal)
    "visible_inventory_score": 6.0,
    "seller_count_score": 5.0,
    "spec_supply_score": 5.0,
    "scarcity_score": 4.0,
    "last_reprint_days_ago": 3.0,
    "single_printing_flag": 4.0,
    "is_reserved": 3.0,
    # Demand / historical priors
    "historical_omission_spike_score": 8.0,
    "precon_spike_type_prior": 6.0,
    "edhrec_inclusion_pct": 4.0,
    "precon_cause_similarity": 4.0,
    "spike_type_mechanic_score": 3.0,
    "mechanic_keyword_density": 3.0,
    # Contextual omission flags
    "is_same_product_omission": 5.0,
    "is_mana_fix_omission": 4.0,
    "combo_with_deck_card": 3.0,
    "entry_price_penalty": 2.0,
    # Theme subscores (copy/clone is NOT here — only via deck synergy when deck is copy-themed)
    "token_score": 2.0,
    "graveyard_score": 2.0,
    # Reduced / deprecated deck-prediction signals
    "rarity_score": 1.0,
    "historical_inclusion_rate": 0.5,
    "p_reprint_heuristic": 0.0,
    "num_precon_printings": 0.5,
}

# Rarity mapping scale (lower = less weight on rarity in features)
RARITY_SCORE_MAP = {
    "common": 0.1,
    "uncommon": 0.25,
    "rare": 0.45,
    "mythic": 0.6,
}

# Spec target ranking
MIN_SYNERGY_FOR_SPEC = 0.06
MIN_SYNERGY_HARD_FLOOR = 0.02

# Minimum thematic fit for spike attribution
SPIKE_MIN_DECK_SYNERGY = 0.15
SPIKE_OBSCURE_EDHREC_MIN = 14000
SPIKE_ALT_EDHREC_MIN = 5800

# Ranking boost from cards that historically spiked as omission upgrades
HISTORICAL_SPIKE_PRIOR_WEIGHT = 0.22
HISTORICAL_SPIKE_SYNERGY_OVERRIDE = 0.25
HISTORICAL_SPIKE_EXCLUDE_THRESHOLD = 0.15
ALT_COMMANDER_SYNERGY_MIN = 0.12
ALT_COMMANDER_SPEC_BOOST = 0.50
# Spec ranking — supply scarcity amplifies omission spikes, but demand drives them.
# Supply curve is intentionally flat: a popular recent card (spec_supply=0.25) still
# moves when omitted; an obscure reserved card nobody plays rarely does.
VINTAGE_SPEC_BOOST = 0.28           # was 0.45 — reduced to prevent obscure reserved cards dominating
VINTAGE_MIN_SUPPLY = 0.85
VINTAGE_MIN_SYNERGY = 0.12
VINTAGE_DEMAND_GATE = 0.232         # edhrec demand must meet this floor to qualify for vintage boost
PROVEN_OMISSION_SPEC_BOOST = 0.55
RESERVED_SPEC_BOOST = 0.18          # was 0.30 — reserved cards still get a boost but not overwhelming
ML_SPIKE_NO_PRIOR_DAMPEN = 0.65
SPEC_SUPPLY_EXPONENT = 0.10         # was 1.35 — nearly flat: supply amplifies but doesn't dominate
SPEC_DEMAND_BLEND = 0.05            # was 0.35 — demand drives the score, minimal fixed baseline

# Spec pool / grading — rank and grade at most this many targets
MAX_SPEC_TOP_N = 10

# ML inference — only build features / full-score this many omitted candidates
ML_INFERENCE_PREFILTER_TOP_K = 500

# Commander Spellbook (EDHREC combo data partner) — infinite loop lookup
COMMANDER_SPELLBOOK_API = "https://backend.commanderspellbook.com"
SPELLBOOK_COMBO_CACHE_DIR = DATA_DIR / "cache" / "spellbook"
SPELLBOOK_COMBO_RATE_LIMIT_SEC = 0.15
COMBO_SPEC_BOOST = 0.20
# When False, combo enrichment never hits the network — uses cached variants only
# and degrades gracefully (no combo signal) if Spellbook is down/unreachable.
# Prevents per-card 45s timeouts from freezing a backtest. Set MTG_COMBO_OFFLINE=1.
SPELLBOOK_FETCH_LIVE = os.getenv("MTG_COMBO_OFFLINE", "") not in ("1", "true", "True")

MTGJSON_CACHE_DIR = DATA_DIR / "cache" / "mtgjson"
ALL_PRICES_PATH = MTGJSON_CACHE_DIR / "AllPrices.json"
ALL_PRICES_GZ = MTGJSON_CACHE_DIR / "AllPrices.json.gz"

# Canonical spike bible — classified spikes with causes, types, and sources
SPIKE_DATA_XLSX_PATH = DATA_DIR / "raw" / "Spike Data.xlsx"
SPIKE_PRECON_REASONING_CSV_PATH = DATA_DIR / "raw" / "Spike Data - Pre-Con Spike Reasoning.csv"
SPIKE_REASONING_SHEET = "Spike Reasoning"
SPIKE_PRECON_DECK_COLUMN = "Pre-con Deck Name"
SPIKE_PRECON_SET_COLUMN = "Pre-con Set Code"
SPIKE_ALL_SPIKES_SHEET = "All Spikes"

# Primary spike index for backtesting, grading, and historical priors
SPIKE_CSV_PATH = SPIKE_DATA_XLSX_PATH
SPIKE_REASONING_CSV_PATH = SPIKE_DATA_XLSX_PATH

# Raw TCGPlayer export (ingest / cleaning only — not used by the model)
SPIKE_TCGPLAYER_CSV_PATH = (
    DATA_DIR / "raw" / "mtg_price_spikes_historical - All Spikes.csv"
)
