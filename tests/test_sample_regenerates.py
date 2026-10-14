"""The committed sample data is exactly what scripts/generate_sample.py produces."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "data" / "sample"


def test_generator_reproduces_committed_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location(
        "generate_sample", ROOT / "scripts" / "generate_sample.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # dataclasses look the module up
    spec.loader.exec_module(module)
    module.generate(tmp_path)

    generated = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    committed = sorted(p.relative_to(SAMPLE) for p in SAMPLE.rglob("*") if p.is_file())
    assert generated == committed
    for rel in generated:
        assert (tmp_path / rel).read_bytes() == (SAMPLE / rel).read_bytes(), rel
