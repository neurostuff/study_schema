import pytest

from study_schema.models.paper_parse import ReportedSpace
from study_schema.spaces import SPACES, normalize_space


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MNI", "MNI"),
        ("mni", "MNI"),
        (" MNI152 ", "MNI"),
        ("MNI-152", "MNI"),
        ("MNI152 2mm", "MNI"),
        ("MNI (Montreal Neurological Institute)", "MNI"),
        ("ICBM152 2009c", "MNI"),
        ("MNI/ICBM", "MNI"),
        ("MNI152NLin2009cAsym", "MNI"),
        ("Colin27", "MNI"),
        ("MNI space", "MNI"),
        ("TAL", "TAL"),
        ("tal", "TAL"),
        ("Talairach", "TAL"),
        ("TALAIRACH", "TAL"),
        ("Talairach & Tournoux 1988", "TAL"),
        ("Talairach and Tournoux (1988)", "TAL"),
        ("Talairach space coordinates", "TAL"),
        ("T&T", "TAL"),
        ("tal88", "TAL"),
        ("TT", "TAL"),
        ("TT88", "TAL"),
        ("TLRC", "TAL"),
        ("+tlrc", "TAL"),
        ("TT_N27", "TAL"),
        ("T88", "TAL"),
        # Names both spaces: which one the numbers are in is not decidable.
        ("MNI converted to Talairach", None),
        ("mni2tal", None),
        ("tal2mni", None),
        ("tal2icbm", None),
        ("icbm2tal", None),
        ("unknown space", None),
        ("UNKNOWN", None),
        ("n.a.", None),
        ("N/A", None),
        ("not applicable", None),
        ("none reported", None),
        ("missing", None),
        ("null", None),
        ("?", None),
        ("-", None),
        ("not reported", None),
        ("other", "OTHER"),
        ("OTHER", "OTHER"),
        ("native", "OTHER"),
        ("fsaverage", "OTHER"),
        ("MNI surface", "MNI"),
        ("Paxinos and Watson", "OTHER"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_space(raw, expected):
    assert normalize_space(raw) == expected


def test_reported_space_is_the_normalized_vocabulary():
    assert tuple(member.value for member in ReportedSpace) == SPACES
    for space in SPACES:
        assert normalize_space(space) == space
