"""The LinkML schemas the package is generated from, as paths into the installed package.

    EXTRACTION     neuroimaging-study-extraction.yaml   pondie -> ingester
    STORAGE        neuroimaging-study-storage.yaml      ingester -> neurostore
    PAPER_PARSE    neuroimaging-paper-parse.yaml        ingestion -> pondie, ingester
"""

from pathlib import Path

_DIRECTORY = Path(__file__).parent

EXTRACTION = _DIRECTORY / "neuroimaging-study-extraction.yaml"
STORAGE = _DIRECTORY / "neuroimaging-study-storage.yaml"
PAPER_PARSE = _DIRECTORY / "neuroimaging-paper-parse.yaml"
