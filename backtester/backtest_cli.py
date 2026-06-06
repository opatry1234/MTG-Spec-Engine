"""
Offline backtest runner — backward-compatible wrapper.

Prefer the unified CLI:

    python -m cli batch --limit 20 --use-ml
    python -m cli deck --name "Eternal Might" --use-ml

Legacy usage still works:

    python backtester/backtest_cli.py batch --top-n 20
"""

from cli.commands.backtest import backtest_only_main

if __name__ == "__main__":
    backtest_only_main()
