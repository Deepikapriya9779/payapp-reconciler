from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from payapp_reconciler.cli import load_dotenv, main

SAMPLE = Path(__file__).parents[1] / "data" / "sample"


def test_cli_writes_all_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main([str(SAMPLE), "--sub", "elec", "--app", "3", "--memo", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Copperline Electric" in out and "Current Payment Due" in out
    assert "R06" in out and "held at 10%" in out
    folder = tmp_path / "elec_app_3"
    assert {p.name for p in folder.iterdir()} == {"findings.json", "review.xlsx", "memo.md"}
    assert json.loads((folder / "findings.json").read_text())["variance"] == "-54720.00"
    assert "memo.md (template: LLM_MODE=mock)" in out


def test_cli_clean_app_and_threshold_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(SAMPLE), "--sub", "dryw", "--app", "2", "--out", str(tmp_path)]) == 0
    assert "R09" in capsys.readouterr().out
    args = [str(SAMPLE), "--sub", "dryw", "--app", "2", "--out", str(tmp_path)]
    assert main([*args, "--front-load-threshold", "50"]) == 0
    assert "application ties out" in capsys.readouterr().out
    assert not (tmp_path / "dryw_app_2" / "memo.md").exists()


def test_cli_bad_input_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(SAMPLE), "--sub", "nobody", "--app", "1", "--out", str(tmp_path)]) == 2
    assert "Input error" in capsys.readouterr().out


def test_load_dotenv_does_not_override_real_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nANTHROPIC_MODEL="claude-opus-5-5"\nLLM_MODE=live\nEMPTY=\n')
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    load_dotenv(env)
    assert os.environ["ANTHROPIC_MODEL"] == "claude-opus-5-5"
    assert os.environ["LLM_MODE"] == "mock"  # set by the test fixture, so .env doesn't win
    assert "EMPTY" not in os.environ
    monkeypatch.delenv("ANTHROPIC_MODEL")
