"""The committed models and JSON Schemas are what the YAML generates today."""

import pytest

pytest.importorskip("linkml", reason="regeneration needs the `generate` extra")


def test_generated_files_are_current(generator, capsys):
    assert generator.main(["--check"]) == 0, capsys.readouterr().out
