"""
SQLAlchemy ORM Models for MTG Spec Engine.

Defines the database schema for cards, decks, prices, and features.
"""

from sqlalchemy import Column, String, Integer, REAL, Boolean, Date, DateTime, Text, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


class Card(Base):
    """Oracle card data from Scryfall."""
    __tablename__ = "cards"
    
    id = Column(String, primary_key=True)  # Scryfall UUID
    name = Column(String, nullable=False)
    mana_cost = Column(String)
    cmc = Column(REAL)
    colors = Column(JSON)  # ["W", "U"]
    color_identity = Column(JSON)
    type_line = Column(String)
    oracle_text = Column(Text)
    keywords = Column(JSON)
    creature_types = Column(JSON)
    rarity = Column(String)
    set_code = Column(String)
    released_at = Column(Date)
    reserved = Column(Boolean, default=False)
    edhrec_rank = Column(Integer)
    image_url = Column(String)
    image_path = Column(String)
    commander_legal = Column(Boolean)
    layout = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CardPrinting(Base):
    """Individual card printings with set information."""
    __tablename__ = "card_printings"
    
    id = Column(String, primary_key=True)  # Scryfall print UUID
    card_name = Column(String)
    set_code = Column(String)
    released_at = Column(Date)
    is_commander_precon = Column(Boolean, default=False)


class CommanderDeck(Base):
    """Commander precon products."""
    __tablename__ = "commander_decks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    product = Column(String)  # "Bloomburrow Commander"
    deck_name = Column(String)
    release_date = Column(Date)
    precon_release_date = Column(Date)  # shelf date when it differs from set release
    decklist_reveal_date = Column(Date)  # spec anchor — day the full decklist became public
    theme = Column(String)
    colors = Column(JSON)
    commander_name = Column(String)
    commander_text = Column(Text)
    total_cards = Column(Integer, default=100)
    new_cards = Column(Integer)
    land_count = Column(Integer)
    announced_new_cards = Column(Integer)
    product_description = Column(Text)
    source_url = Column(String)
    include_in_training = Column(Boolean, default=True)
    decklist_revealed = Column(Boolean, default=False)
    deck_composition = Column(JSON)  # cached skeleton counts by card type (legacy)
    deck_features = Column(JSON)  # full deck feature vector (composition, functional, new/reprint)
    product_type = Column(String)
    release_era = Column(String)
    primary_archetype = Column(String)
    secondary_archetype = Column(String)


class DeckCard(Base):
    """Junction table: cards in decks."""
    __tablename__ = "deck_cards"
    
    deck_id = Column(Integer, ForeignKey("commander_decks.id"), primary_key=True)
    card_name = Column(String, primary_key=True)
    quantity = Column(Integer, default=1)
    is_new_card = Column(Boolean, default=False)


class PriceHistory(Base):
    """Historical price data."""
    __tablename__ = "price_history"
    
    card_name = Column(String, primary_key=True)
    date = Column(Date, primary_key=True)
    price_usd = Column(REAL)
    price_foil = Column(REAL)
    source = Column(String, default="scryfall", primary_key=True)


class SupplySnapshot(Base):
    """Live supply data from TCGAPIs."""
    __tablename__ = "supply_snapshots"
    
    card_name = Column(String, primary_key=True)
    snapshot_date = Column(Date, primary_key=True)
    tcg_listing_count = Column(Integer)
    tcg_price_low = Column(REAL)
    tcg_price_market = Column(REAL)
    tcg_price_high = Column(REAL)
    cm_listing_count = Column(Integer)
    cm_price_low = Column(REAL)
    cm_price_trend = Column(REAL)
    total_listings = Column(Integer)
    supply_spread = Column(REAL)


class StaplesExclusionList(Base):
    """Cards that should never be in the candidate pool."""
    __tablename__ = "staples_exclusion_list"
    
    card_name = Column(String, primary_key=True)
    reason = Column(String)
    added_date = Column(Date)
    added_by = Column(String, default="manual")


class CardFeatures(Base):
    """Computed features for ML training and prediction."""
    __tablename__ = "card_features"
    
    run_id = Column(Integer, primary_key=True)
    card_name = Column(String, primary_key=True)
    deck_id = Column(Integer, primary_key=True)
    
    # Mechanical
    color_identity_match = Column(Boolean)
    creature_type_overlap = Column(REAL)
    keyword_overlap_score = Column(REAL)
    token_score = Column(REAL)
    graveyard_score = Column(REAL)
    copy_score = Column(REAL)
    
    # NLP
    tfidf_similarity = Column(REAL)
    embedding_similarity = Column(REAL)
    
    # Supply
    num_printings = Column(Integer)
    num_precon_printings = Column(Integer)
    last_reprint_days_ago = Column(Integer)
    is_reserved = Column(Boolean)
    set_age_years = Column(REAL)
    rarity_score = Column(REAL)
    current_price = Column(REAL)
    tcg_listing_count = Column(Integer)
    cm_listing_count = Column(Integer)
    total_listings = Column(Integer)
    supply_spread = Column(REAL)
    
    # Popularity
    edhrec_rank = Column(Integer)
    edhrec_inclusion_pct = Column(REAL)
    
    # Labels
    label_included = Column(Boolean)
    label_reprinted = Column(Boolean)


class CardPrice(Base):
    """Local mirror of Supabase card_prices_current (synced before scoring)."""
    __tablename__ = "card_prices"

    card_name = Column(String, primary_key=True)
    price_usd = Column(REAL)
    price_usd_foil = Column(REAL)
    available_copies = Column(Integer)
    seller_count = Column(Integer)
    copies_per_seller = Column(REAL)
    as_of_date = Column(Date)


class VolumeHistory(Base):
    """Local mirror of Supabase card_prices_history volume rows (weekly sales).

    Synced from card_prices_history where quantity_sold is not null. Unlike price
    (forward-only), this is a real 12-month historical series, so velocity is
    point-in-time computable for any anchor inside the captured window.
    """
    __tablename__ = "volume_history"

    card_name = Column(String, primary_key=True)
    snapshot_date = Column(Date, primary_key=True)
    quantity_sold = Column(Integer)
    transaction_count = Column(Integer)
    market_price = Column(REAL)


class PredictionRun(Base):
    """Metadata for each prediction run."""
    __tablename__ = "prediction_runs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    deck_id = Column(Integer, ForeignKey("commander_decks.id"))
    run_date = Column(DateTime, default=datetime.utcnow)
    model_version = Column(String)
    reprint_ceiling = Column(Integer)
    notes = Column(Text)
