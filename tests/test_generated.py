"""The committed models and JSON Schemas are what the YAML generates today."""

import importlib.util
import sys

import pytest

from conftest import ROOT

pytest.importorskip("linkml", reason="regeneration needs the `generate` extra")


def test_generated_files_are_current(capsys):
    spec = importlib.util.spec_from_file_location("generate_models", ROOT / "tools" / "generate_models.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_models"] = module
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, capsys.readouterr().out
