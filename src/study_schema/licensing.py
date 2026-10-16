"""Whether a licence, and the way a copy was obtained, let us pass a text on.

Nothing in a parsed paper or a record says this; it is derived here from the facts they
hold. ``REDISTRIBUTABLE_LICENSES`` is the LicenseCode values annotated ``redistributable`` in
neuroimaging-study-storage/license.yaml, and ``NEVER_REDISTRIBUTED_ACCESS`` the CopyAccess
values annotated ``redistribution: never`` in neuroimaging-paper-parse/parse_enums.yaml;
tests/test_licensing.py keeps them equal.

Two uses:

- The parsed text: ``permits_redistribution(paper.text_copy.license, paper.text_copy.access)``.
- The abstract: ``permits_redistribution(paper.bibliography.license)``. The indexers carry the
  publisher's abstract of the version that licence describes.
"""

from typing import Any, Mapping, Optional

REDISTRIBUTABLE_LICENSES = frozenset(
    {
        "cc-by",
        "cc-by-sa",
        "cc-by-nc",
        "cc-by-nc-sa",
        "cc-by-nd",
        "cc-by-nc-nd",
        "cc0",
        "public-domain",
    }
)
NEVER_REDISTRIBUTED_ACCESS = frozenset({"tdm_grant", "unrecorded"})


def _value(item: Any) -> Optional[str]:
    return getattr(item, "value", item)


def permits_redistribution(license: Any, access: Any = None) -> bool:
    """True when ``license`` lets the text be passed on and ``access`` does not forbid it.

    ``license`` is a License dict or model. ``access`` is a held copy's CopyAccess, or None
    for a text we do not hold a copy of, such as an indexer's abstract.
    """
    code = license.get("code") if isinstance(license, Mapping) else license.code
    if _value(code) not in REDISTRIBUTABLE_LICENSES:
        return False
    return _value(access) not in NEVER_REDISTRIBUTED_ACCESS
