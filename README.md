# MTG Commander Precon Spec Engine

A local, data-driven system for ranking **omitted-card spec targets** once a Commander precon decklist is public, anchored to the **decklist reveal date**.

## Quick Start

### Setup

1. Create and activate virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Initialize database:
   ```bash
   cd db
   python init_db.py
   cd ..
   ```

### First Run

1. Ingest Scryfall data:
   ```bash
   python ingest/scryfall.py --mode oracle
   python ingest/scryfall.py --mode printings
   ```

2. Load historical decklist:
   ```bash
   python ingest/decklists.py --csv data/decklists/your_decklist.csv
   ```

3. Build features:
   ```bash
   python features/builder.py --deck-id 1
   ```

4. Launch UI:
   ```bash
   streamlit run app.py
   ```

## Architecture

See `../mtg_precon_spec_engine_architecture.md` for complete system design.

### Directory Structure

- `app.py` — Streamlit entrypoint
- `config.py` — API keys and constants
- `db/` — Database schema and initialization
- `ingest/` — Data pipelines (Scryfall, prices, supply)
- `filters/` — Staples exclusion and slot calculation
- `features/` — Feature engineering (mechanical, NLP, supply)
- `models/` — ML models (inclusion, reprint, omission)
- `backtester/` — Backtesting framework
- `ui/` — Streamlit pages and components
- `data/` — Raw data, decklists, model files
- `tests/` — Unit tests

## Configuration

Set API keys as environment variables:

```bash
export TCGAPIS_KEY="your_api_key_here"
```

## Development

Run tests:
```bash
pytest tests/
```

## Command-line interface (full terminal control)

From `mtg_spec_engine/` with venv active:

```bash
python -m cli --help
```

Or use the wrapper script:

```bash
chmod +x mtg-spec
./mtg-spec batch --limit 30 --use-ml
```

### Typical iteration loop (train → evaluate)

```bash
# Train fresh models, then batch backtest with letter grades saved to JSON/CSV
python -m cli iterate --train --limit 30 --use-ml --top-n 20 --run-id my_run_01

# Or step by step:
python -m cli train --holdout
python -m cli batch --limit 30 --use-ml --grade
python -m cli results list
python -m cli results show my_run_01
```

### Command reference

| Command | Purpose |
|---------|---------|
| `batch` | Batch card backtest → `data/analytics/backtest_runs/<id>/` |
| `deck` | Single-deck backtest (`--save` or `--json-out`) |
| `skeleton` | Skeleton MAE across training decks |
| `train` | Train inclusion + reprint + spec-spike models |
| `models list` | List saved model versions |
| `iterate` | `--train` then batch in one shot |
| `predict` | Rank spec targets from announcement text |
| `ingest scryfall` | Scryfall oracle + printings |
| `ingest enrich` | TCGPlayer descriptions + commander text |
| `ingest decklists` | Load decklist CSVs into DB |
| `data deck-features` | Rebuild `deck_features` |
| `db init` / `db migrate --all` | Database setup |
| `combo deck` / `combo all` | Combo validation vs spike bible |
| `results list` / `results show` | Browse saved runs |
| `ui` | Launch Streamlit (`streamlit run app.py`) |

Legacy entry points still work: `python backtester/backtest_cli.py batch`, `python -m models.trainer_cli`.

## Documentation

- Full architecture: See `../mtg_precon_spec_engine_architecture.md`
- Database schema: See `db/schema.py`
- ML models: See `models/` directory
