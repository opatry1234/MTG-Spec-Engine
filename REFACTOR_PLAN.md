# Phase-3-Only Refactor Plan

**Status: completed** (2026-06-04)

## Completed checklist

- [x] Schema + `decklist_reveal_date` CSV ingested (161/161)
- [x] `spec_anchor_date()` drives feature as-of, eligibility, spike floor
- [x] Deck-prediction modules removed; pipeline scores omitted cards only
- [x] `ScoringContext` replaces 3-stage timeline; UI stage slider removed
- [x] Feature set updated (surprising omission, deck synergy direct, combo flags)
- [x] Grading re-enabled at Phase 3 with reveal-anchored golden specs
- [x] Tests rescoped for Phase 3

## Original plan

Goal: collapse the engine to a single workflow — **decklist is public, score cards that synergize with the commander/deck and are *not* in the list, anchored to the decklist-reveal date.** Strip everything whose only purpose was to *predict the deck*, since the deck is now given.

---

## 1. Locked decisions (from Q&A)

| Decision | Answer |
|---|---|
| Deck-prediction machinery | **Rip out fully** (keep reprint *risk* as a feature) |
| Anchor date | **Spec anchor = decklist-reveal date** (not buy/street date) |
| Spike ground truth | **Curated Spike Reasoning sheet** stays authoritative for grading |
| ML | **Keep all three models** (inclusion / reprint / spec_spike) |

### ⚠️ One conflict to confirm
"Rip out fully" lists *deleting* the inclusion & reprint models; "Keep all three models" *retains* them. They were scored on different axes, so this overlap is easy to miss.

**Proposed reconciliation (my recommendation):** keep all three trained ML models, but delete the *heuristic deck-reconstruction scaffolding* around them — `skeleton_predictor`, `slot_constraints`, `probable_deck`, `wizards_inclusion` / `p_wizards`, and the 3-stage `release_timeline`. The models change *role* rather than disappear:

- **inclusion model → "surprising-omission" signal.** In Phase 3 we *know* the card is omitted. A card the model expected to be included (high p_include) but that was left out is a *stronger* spec — Wizards "should" have run it. So inclusion probability becomes a feature, not a deck builder.
- **reprint model → reprint-risk suppressor.** A card likely to be reprinted soon is a worse spec. Unchanged in spirit.
- **spec_spike model → the primary ranker.** Target = "card spiked post-anchor, attributed to this precon."

If instead you literally want the inclusion & reprint *models* gone (pure single-model + heuristic), say so and I'll cut them. **Everything below assumes the reconciliation above.**

---

## 2. What gets deleted

Whole modules (deck-prediction only):

- `models/skeleton_predictor.py`, `analytics/skeleton.py`, `backtester/skeleton_metrics.py`, `analytics/historical_distributions.py` (slot ratios)
- `engine/slot_constraints.py`, `filters/slot_calculator.py`
- `engine/probable_deck.py`, `engine/wizards_inclusion.py`
- `engine/release_timeline.py` → replaced by a tiny `ScoringContext` (no stages)
- Skeleton/slot/probable-deck branches inside `engine/pipeline.py` and `engine/heuristic_scorer.py`
- Tests: `test_skeleton.py`, `test_slot_constraints.py`, `test_probable_deck.py`, `test_wizards_inclusion.py`, `test_decklist_guess.py`, `test_point_in_time_features.py` (rescope)

Config constants to delete: everything under "skeleton stratification," `AVERAGE_LANDS_PER_PRECON`, `WIZARDS_*_INCLUSION_THRESHOLD`, `ALT_COMMANDER_*`, the announcement/shelf spike windows, and the `release_stage` plumbing.

The pipeline shrinks from **commander → skeleton → slots → probable deck → spec pool** down to **commander + decklist → candidate filter → score omitted cards → rank**.

---

## 3. New anchor: decklist-reveal date

The current as-of date is `deck.release_date` (set street date). We switch the whole engine to a **decklist-reveal date**, which is *earlier* than street date and is the real information event.

Work:

1. **Schema:** add `decklist_reveal_date` to `commander_decks` (migration). Falls back to `release_date` when unknown.
2. **Scraper:** new `ingest/decklist_reveal_dates.py`. 161 decks, 136 have `source_url`. Sources to mine, in priority order: the stored `source_url` (often the official Wizards/EDHREC reveal article), Scryfall set `released_at` minus known reveal-lead, and the existing Spike Reasoning sheet (its spike dates bound the reveal from above). I'll produce a coverage report and flag any deck I can't date confidently for manual fill.
3. **Eligibility cutoff:** `was_spec_eligible_at_prerelease()` → rename `was_spec_eligible_at_reveal()`, cutoff = `decklist_reveal_date`. Cards first printed after reveal are ineligible (you couldn't own them).
4. **Spike-timing floor:** a spike only counts if its date is **≥ decklist_reveal_date**. This replaces the announcement/shelf window logic and `SPIKE_PRE_RELEASE_GRADEABLE`. Spikes before reveal are discarded — you couldn't have bought the low.

---

## 4. Feature audit

### Drop (deck-prediction leakage or now-meaningless)
- `historical_inclusion_rate` as a *primary* score driver — the deck is known; keep only as a weak inclusion-model feature.
- `color_identity_match` as a feature — it's now a hard *filter* (candidates are color-legal by construction), not a learned signal.
- Skeleton/slot composition features, `p_wizards`-derived columns.

### Keep (core spec signals)
- `tfidf_similarity`, `creature_type_overlap`, `keyword_overlap_score`, `token/graveyard/copy_score` → synergy with commander/deck.
- `scarcity_score`, `spec_supply_score`, `num_printings`, `last_reprint_days_ago`, `is_reserved`, `single_printing_flag` → supply side.
- `edhrec_inclusion_pct` / demand → but **fix the leakage** (resolved): keep raw present-day EDHREC on the **live** path (no future to leak), and on the **backtest** path swap to a reveal-date proxy (price level + TCG listing velocity + prior-deck inclusion), never present-day rank.
- `historical_omission_spike_score`, `precon_spike_type_prior`, `precon_cause_similarity` → proven-omission priors.
- `p_reprint_heuristic` → reprint-risk suppressor.

### Add (new, enabled by Phase-3 certainty)
- **`surprising_omission_score`** = inclusion-model p_include × (card is omitted) — the headline new feature.
- **`deck_synergy_direct`** — synergy measured against the *actual* commander + the *actual* 99 cards (co-occurrence / combo adjacency to cards really in the list), not just theme text. We finally have the real list.
- **`combo_with_deck_card`** — infinite/2-card combo where the *other half is in the decklist* (Commander Spellbook). Strong, currently underused at Phase 3.
- **`reveal_to_spike_days` (label-side)** — for analysis: how fast post-reveal spikes happen, to tune the spike window.
- **`mana_fix_omission` / `same_product_omission`** — already partly present; promote to first-class since omission is now certain.
- **`entry_price_at_reveal` (inverse) + `volume_upside`** — a card already expensive at reveal is a *worse* spec: less multiple upside and fewer buyable copies. The ideal target is a cheap, high-supply card with demand evidence (100× $0.50→$8 beats 10× $10→$20). So absolute reveal price enters as a *penalty*, and demand must come from inclusion/listing velocity rather than price level alone. This is now the headline scoring principle, not just a feature.

---

## 5. Target the model should reproduce

For each historical precon, the curated sheet names the cards that spiked *because of it*. Success = the model, **blind to the sheet**, surfaces those cards in its top-N from the omitted-card pool, with every qualifying spike dated **≥ decklist_reveal_date**. Grading (`backtester/grade.py`) stays sheet-based but its eligibility + spike-window checks switch to the reveal anchor.

---

## 6. Execution order

1. Schema migration + `decklist_reveal_date` scraper + coverage report. *(data first — everything keys off this)*
2. Rip out deck-prediction modules; collapse `pipeline.py` / `heuristic_scorer.py` to the omitted-card scorer; delete dead config.
3. Re-anchor eligibility + spike window to reveal date.
4. Rework `features/builder.py`: drop dead features, add the four new ones, fix EDHREC leakage.
5. Retrain three models on the new feature set + labels; rerun batch backtest; compare golden-spec recall vs. the old run.
6. Prune/rescope tests; update README + skill.

I'd do **step 1 first as a standalone deliverable** (the reveal-date dataset is reusable no matter what), then pause for you to eyeball coverage before the demolition.

---

## Open questions before I start
1. Confirm the model reconciliation in §1 (keep all three as repurposed features vs. literally delete inclusion+reprint).
2. EDHREC at reveal date — OK to use a **stable proxy** (e.g. first-captured rank, or demand bucket) rather than chasing historical EDHREC snapshots we don't have?
3. Start with **step 1 (reveal-date scraper)** and pause for review, or do you want the full demolition in one pass?
