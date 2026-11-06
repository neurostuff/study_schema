"""Which release of each vocabulary a record's `normalized` entries resolve against.

A `Normalization.vocabulary` names one of the vocabularies in ``vocabularies.json``, or
an enum this schema defines (``AgeUnit``), and its ``value`` is an id of that vocabulary's
pinned release. The manifest names each vocabulary once, neutrally, so that a reader
(neurostore's ancestor table, a query) loads the same release the normalizer mapped
against. Records written before the rename carry the legacy strings, which `for_legacy`
reads back.

    vocabularies()                                 -> every Vocabulary, in manifest order
    vocabulary("mondo").download.url               -> the pinned release's URL
    for_legacy("ns-pond contrast set fit-ed5ac7b08c20")
                                                   -> the contrast_set Vocabulary
"""

from __future__ import annotations

import functools
import json
import re
from importlib import resources
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Vocabulary", "for_legacy", "vocabularies", "vocabulary"]

_SHA256 = r"^[0-9a-f]{64}$"
#: What each placeholder in a legacy vocabulary string stands for.
_PLACEHOLDERS = {"axis": r"[a-z_]+", "fit_id": r"fit-[0-9a-f]{12}"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Download(_Strict):
    url: str
    sha256: str = Field(pattern=_SHA256)
    bytes: int = Field(gt=0)


class Source(_Strict):
    """A file the vocabulary is defined by: in a repository at a commit, or (a fit) neither."""

    repository: Optional[str]
    commit: Optional[str] = Field(pattern=r"^[0-9a-f]{40}$")
    path: str
    sha256: str = Field(pattern=_SHA256)
    role: str


class Hierarchy(_Strict):
    """Where an id's parent comes from: an edge in the release, a field, or the id's prefix."""

    kind: Literal["is_a", "skos:broader", "field", "prefix"]
    parent_vocabulary: Optional[str]
    field: Optional[str] = None
    separator: Optional[str] = None
    description: str


class Vocabulary(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z_]*$")
    title: str
    legacy_vocabularies: list[str] = Field(min_length=1)
    release: str
    download: Optional[Download]
    sources: list[Source]
    id_form: str
    hierarchy: Optional[Hierarchy]
    values: Optional[list[str]]
    notes: Optional[str]


class _Manifest(_Strict):
    manifest_version: int
    vocabularies: list[Vocabulary]


@functools.lru_cache(maxsize=1)
def _manifest() -> _Manifest:
    text = resources.files("study_schema").joinpath("vocabularies.json").read_text()
    return _Manifest.model_validate(json.loads(text))


def vocabularies() -> list[Vocabulary]:
    return list(_manifest().vocabularies)


def vocabulary(name: str) -> Vocabulary:
    """The vocabulary a manifest name names; KeyError for any other string."""
    for v in _manifest().vocabularies:
        if v.name == name:
            return v
    raise KeyError(name)


def _legacy_pattern(template: str) -> re.Pattern:
    parts = re.split(r"\{(\w+)\}", template)
    return re.compile(
        "".join(_PLACEHOLDERS[p] if i % 2 else re.escape(p) for i, p in enumerate(parts)) + "$"
    )


def for_legacy(string: str) -> Optional[Vocabulary]:
    """The vocabulary a pre-rename `Normalization.vocabulary` string means, or None."""
    for v in _manifest().vocabularies:
        if any(_legacy_pattern(t).match(string) for t in v.legacy_vocabularies):
            return v
    return None
