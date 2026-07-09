"""Tests for FAA registry parsing (no network, no DB)."""

import io
import zipfile

from lowdown.faa import _load_acftref


def _zip_with(**members: str) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_load_acftref_maps_code_to_category_and_model():
    # Note the UTF-8 BOM on the first column name, as in the real file.
    acftref = (
        "﻿CODE,MFR,MODEL,TYPE-ACFT,TYPE-ENG,AC-CAT\n"
        "H1234,EUROCOPTER,EC 135 P2+,6,3,\n"
        "F5678,CESSNA,172S,4,1,\n"
        "G9999,SCHEMPP,DISCUS,1,0,\n"
    )
    zf = _zip_with(**{"ACFTREF.txt": acftref})
    ref = _load_acftref(zf)

    assert ref["H1234"] == ("rotorcraft", "EUROCOPTER EC 135 P2+")
    assert ref["F5678"] == ("fixed-wing", "CESSNA 172S")
    assert ref["G9999"] == ("glider", "SCHEMPP DISCUS")
