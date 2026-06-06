"""
Train ML models — backward-compatible wrapper.

Prefer: python -m cli train
"""

from cli.commands.train import cmd_train
import argparse


def main():
    cmd_train(
        argparse.Namespace(
            max_decks=None,
            holdout=False,
            holdout_cutoff="2023-01-01",
            summary=True,
            json_out=None,
        )
    )


if __name__ == "__main__":
    main()
