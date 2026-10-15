from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from study_schema import linkml

ROOT = Path(__file__).resolve().parents[1]
LINKML = linkml.EXTRACTION.parent.resolve()
SCHEMAS = ("EXTRACTION", "STORAGE", "PAPER_PARSE")


def _imports(path: Path) -> list[str]:
    document = yaml.safe_load(path.read_text("utf-8")) or {}
    return [entry for entry in document.get("imports", []) if not entry.startswith("linkml:")]


@pytest.mark.parametrize("name", SCHEMAS)
def test_constant_points_at_a_packaged_schema(name):
    schema = getattr(linkml, name)

    assert isinstance(schema, Path)
    assert schema.is_file()
    assert schema.resolve().is_relative_to(LINKML)


@pytest.mark.parametrize("name", SCHEMAS)
def test_imports_resolve_inside_the_packaged_tree(name):
    pending = [getattr(linkml, name)]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for target in _imports(current):
            imported = (current.parent / f"{target}.yaml").resolve()
            assert imported.is_file(), f"{current.name} imports {target!r}, which is not packaged"
            assert imported.is_relative_to(LINKML)
            pending.append(imported)
    assert len(seen) > 1


def test_wheel_ships_the_linkml_tree(tmp_path):
    pytest.importorskip("setuptools.build_meta", reason="building a wheel needs setuptools")
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pyproject.toml", "README.md"):
        shutil.copy(ROOT / name, project / name)
    shutil.copytree(ROOT / "src", project / "src")
    wheels = tmp_path / "wheels"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from setuptools import build_meta; print(build_meta.build_wheel(sys.argv[1]))",
            str(wheels),
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (wheel,) = wheels.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        shipped = {
            name for name in archive.namelist()
            if name.startswith("study_schema/linkml/") and name.endswith(".yaml")
        }
    expected = {
        f"study_schema/linkml/{path.relative_to(LINKML).as_posix()}"
        for path in LINKML.rglob("*.yaml")
    }
    assert expected
    assert shipped == expected
    assert "study_schema/linkml/extraction-deviations.yaml" in shipped
    assert "study_schema/linkml/extraction-to-storage.map.yaml" in shipped
