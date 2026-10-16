"""Whether a licence, and the way a copy was obtained, let us pass a text on.

Nothing in a parsed paper or a record says this; it is derived here from the facts they
hold. ``REDISTRIBUTABLE_LICENSES`` is the LicenseCode values annotated ``redistributable`` in
neuroimaging-study-storage/license.yaml, and ``REDISTRIBUTABLE_ACCESS`` the CopyAccess values
annotated ``redistribution: by_license`` in neuroimaging-paper-parse/parse_enums.yaml;
tests/test_licensing.py keeps them equal.

Two uses:

- The parsed text: ``permits_redistribution(paper.text_copy.license, paper.text_copy.access)``.
  The text call must always pass the copy's access: without it, a copy obtained under a
  text-mining grant would be judged by its licence alone.
- The abstract: ``permits_redistribution(paper.bibliography.license)``. The indexers carry the
  publisher's abstract of the version that licence describes.

The reading is a policy, not legal advice: every CC licence counts as redistributable,
NC and ND included, and ``other`` never does, so Elsevier's open-access user licence is
refused on purpose. The JSON Schema is the validator of a parse or record; this function
assumes nothing it checks, and answers False for anything it does not recognise as
permitted.
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
REDISTRIBUTABLE_ACCESS = frozenset({"public"})


def _value(item: Any) -> Optional[str]:
    return getattr(item, "value", item)


def _field(license: Any, name: str) -> Any:
    if isinstance(license, Mapping):
        return license.get(name)
    return getattr(license, name, None)


def permits_redistribution(license: Any, access: Any = None) -> bool:
    """True when ``license`` lets the text be passed on and ``access`` does not forbid it.

    ``license`` is a License dict or model. ``access`` is a held copy's CopyAccess, or None
    for a text we do not hold a copy of, such as an indexer's abstract.

    False, never an error, for anything else: no licence, an unknown code or access, or a
    redistributable code that is not stated by a named source in a non-empty statement,
    which the schema's rules forbid (an assumed licence is always all rights reserved).
    """
    if license is None or _value(_field(license, "code")) not in REDISTRIBUTABLE_LICENSES:
        return False
    stated_as = _field(license, "stated_as")
    if (
        _value(_field(license, "basis")) != "stated"
        or not _field(license, "stated_by")
        or not (isinstance(stated_as, str) and stated_as.strip())
    ):
        return False
    return access is None or _value(access) in REDISTRIBUTABLE_ACCESS
