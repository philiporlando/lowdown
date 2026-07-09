"""Tests for the ICAO24 <-> N-number conversion."""

import pytest

from lowdown.nnumber import icao_to_n, n_to_icao


@pytest.mark.parametrize(
    "icao,n",
    [
        ("a00001", "N1"),        # first US address
        ("a00002", "N1A"),       # first single-letter suffix
        ("a00003", "N1AA"),      # first double-letter suffix
        ("adf7c7", "N99999"),    # last US address
    ],
)
def test_known_anchors(icao, n):
    assert icao_to_n(icao) == n
    assert n_to_icao(n) == icao


def test_non_us_returns_none():
    # Addresses outside the US block (e.g. a European registration range).
    assert icao_to_n("400000") is None
    assert icao_to_n("3c6444") is None  # German D-registration block
    assert icao_to_n("not-hex") is None


def test_roundtrip_is_stable():
    # Every valid US address must round-trip through its N-number and back.
    base = 0xA00001
    for offset in range(0, 915_399, 617):  # stride hits all tail shapes
        icao = f"{base + offset:06x}"
        n = icao_to_n(icao)
        assert n is not None
        assert n.startswith("N")
        assert n_to_icao(n) == icao


def test_roundtrip_full_range_edges():
    for offset in (0, 1, 2, 601, 602, 10_711, 101_710, 101_711, 915_398):
        icao = f"{0xA00001 + offset:06x}"
        assert n_to_icao(icao_to_n(icao)) == icao


def test_invalid_n_numbers():
    assert n_to_icao("N0") is None       # can't start with 0
    assert n_to_icao("N1I") is None      # I is not allowed
    assert n_to_icao("N1O") is None      # O is not allowed
    assert n_to_icao("XYZ") is None      # not an N-number
