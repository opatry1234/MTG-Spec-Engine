"""CLI parser smoke tests."""

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from cli.app import build_parser, main

ROOT = Path(__file__).parent.parent


def test_build_parser_has_core_commands():
    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    cmds = set(sub.choices.keys())
    assert "batch" in cmds
    assert "train" in cmds
    assert "iterate" in cmds
    assert "predict" in cmds
    assert "ingest" in cmds
    assert "backtest" in cmds


def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "cli", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "iterate" in result.stdout


def test_main_no_command_exits_nonzero():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0
