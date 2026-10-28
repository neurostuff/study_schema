"""Whether the text or the abstract of a parsed paper may be passed on.

Nothing in a parsed paper or a record says this; it is derived here from the facts they
hold. ``REDISTRIBUTABLE_LICENSES`` is the LicenseId values annotated ``redistributable`` in
neuroimaging-study-storage/license.yaml, and ``REDISTRIBUTABLE_ACCESS`` the CopyAccess values
annotated ``redistribution: by_license`` in neuroimaging-paper-parse/parse_enums.yaml;
tests/test_licensing.py keeps them equal.

Two questions, one function each, so that neither can be asked with the other's facts:

- The parsed text: ``text_may_be_redistributed(paper.text_copy)``. It takes the held copy,
  so the access it was obtained under is always weighed with its licence.
- The abstract: ``abstract_may_be_redistributed(paper.bibliography.license)``. The indexers
  carry the publisher's abstract of the version that licence describes.

The reading is a policy, not legal advice: every CC licence counts as redistributable,
NC and ND included, and ``other`` never does, so Elsevier's open-access user licence is
refused on purpose. The JSON Schema is the validator of a parse or record; these functions
assume nothing it checks, and answer False for anything they do not recognise as
permitted.
"""

from typing import Any, Mapping, Optional

REDISTRIBUTABLE_LICENSES = frozenset(
    {
        "cc_by",
        "cc_by_sa",
        "cc_by_nc",
        "cc_by_nc_sa",
        "cc_by_nd",
        "cc_by_nc_nd",
        "cc0",
        "public_domain",
    }
)
REDISTRIBUTABLE_ACCESS = frozenset({"public"})


def _value(item: Any) -> Optional[str]:
    return getattr(item, "value", item)


def _field(license: Any, name: str) -> Any:
    if isinstance(license, Mapping):
        return license.get(name)
    return getattr(license, name, None)


def _license_permits(license: Any) -> bool:
    """A redistributable licence id, stated by a named source in a non-empty statement."""
    if license is None or _value(_field(license, "license_id")) not in REDISTRIBUTABLE_LICENSES:
        return False
    stated_as = _field(license, "stated_as")
    return (
        _value(_field(license, "determination")) == "stated"
        and bool(_field(license, "stated_by"))
        and isinstance(stated_as, str)
        and bool(stated_as.strip())
    )


def abstract_may_be_redistributed(paper_license: Any) -> bool:
    """True when the paper's licence (a License dict or model, ``Bibliography.license``)
    lets its abstract be passed on.

    False, never an error, for anything else: no licence, an unknown licence id, or a
    redistributable licence id that is not stated by a named source in a non-empty statement,
    which the schema's rules forbid (an assumed licence is always all rights reserved).
    """
    return _license_permits(paper_license)


def text_may_be_redistributed(held_copy: Any) -> bool:
    """True when the held copy the text was read from (a HeldCopy dict or model,
    ``ParsedPaper.text_copy``) may be passed on: its licence permits it and it was
    obtained publicly.

    False, never an error, for anything else: no copy, a copy without an access or with
    one outside CopyAccess, or a licence ``abstract_may_be_redistributed`` would refuse.
    """
    if held_copy is None:
        return False
    return (
        _value(_field(held_copy, "access")) in REDISTRIBUTABLE_ACCESS
        and _license_permits(_field(held_copy, "license"))
    )
