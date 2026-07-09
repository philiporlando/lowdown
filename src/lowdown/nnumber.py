"""Convert between a US ICAO 24-bit address and its tail number (N-number).

US civil aircraft use a deterministic mapping between the 24-bit Mode-S /
ADS-B address (as reported by OpenSky in ``icao24``) and the FAA registration
"N-number", so no lookup service is needed for US-registered aircraft.

Reference: the FAA's published N-number / ICAO address scheme.
"""

from __future__ import annotations

# 24 letters used in N-numbers (the alphabet without I and O).
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"

_ICAO_US_BASE = 0xA00001  # maps to "N1"
_MAX_OFFSET = 915_398  # last valid US offset ("N99999")

# Number of addresses consumed once the first i digits are fixed.
_BUCKET1 = 101_711
_BUCKET2 = 10_111
_BUCKET3 = 951
_BUCKET4 = 35
_SUFFIX_SPACE = 601  # "", 24 single letters, 576 double-letter combos


def _suffix(offset: int) -> str:
    """Map 0..600 to a tail suffix: "", "A", "AA", "AB", ..., "AZ", "B", ..."""
    if offset == 0:
        return ""
    index = offset - 1
    first = _ALPHABET[index // 25]
    rem = index % 25
    if rem == 0:
        return first
    return first + _ALPHABET[rem - 1]


def _suffix_offset(suffix: str) -> int | None:
    """Inverse of :func:`_suffix`. Returns 0..600 or None if invalid."""
    if suffix == "":
        return 0
    if any(c not in _ALPHABET for c in suffix) or len(suffix) > 2:
        return None
    first = _ALPHABET.index(suffix[0])
    if len(suffix) == 1:
        return first * 25 + 1
    return first * 25 + _ALPHABET.index(suffix[1]) + 2


def icao_to_n(icao24: str) -> str | None:
    """Return the US N-number for an ICAO24 hex address, or None if not US."""
    try:
        value = int(icao24, 16)
    except (ValueError, TypeError):
        return None

    offset = value - _ICAO_US_BASE
    if offset < 0 or offset > _MAX_OFFSET:
        return None  # not a US-registered address

    out = ["N"]
    d1, offset = divmod(offset, _BUCKET1)
    out.append(str(d1 + 1))
    if offset < _SUFFIX_SPACE:
        return "".join(out) + _suffix(offset)

    offset -= _SUFFIX_SPACE
    d2, offset = divmod(offset, _BUCKET2)
    out.append(str(d2))
    if offset < _SUFFIX_SPACE:
        return "".join(out) + _suffix(offset)

    offset -= _SUFFIX_SPACE
    d3, offset = divmod(offset, _BUCKET3)
    out.append(str(d3))
    if offset < _SUFFIX_SPACE:
        return "".join(out) + _suffix(offset)

    offset -= _SUFFIX_SPACE
    d4, offset = divmod(offset, _BUCKET4)
    out.append(str(d4))
    if offset == 0:
        return "".join(out)

    offset -= 1
    if offset < 24:
        out.append(_ALPHABET[offset])  # single trailing letter
    else:
        out.append(str(offset - 24))  # fifth digit
    return "".join(out)


def n_to_icao(n_number: str) -> str | None:
    """Return the lowercase ICAO24 hex for a US N-number, or None if invalid."""
    s = n_number.strip().upper()
    if not s.startswith("N") or len(s) < 2:
        return None
    s = s[1:]

    # Split leading digits (d1 plus middle digits) from the trailing letters.
    i = 0
    while i < len(s) and s[i].isdigit():
        i += 1
    digits, suffix = s[:i], s[i:]
    if not digits or digits[0] == "0":
        return None
    if any(c not in _ALPHABET for c in suffix) or len(suffix) > 2:
        return None

    offset = (int(digits[0]) - 1) * _BUCKET1
    mids = digits[1:]  # d2..d5, at most 4 of them
    buckets = [_BUCKET2, _BUCKET3, _BUCKET4]

    if len(mids) <= 2:
        # Stops after these bucketed digits; terminal is 0-2 letters.
        for pos, ch in enumerate(mids):
            offset += _SUFFIX_SPACE + int(ch) * buckets[pos]
        offset += _suffix_offset(suffix)  # type: ignore[operator]
    elif len(mids) == 3:
        # d2 d3 d4 bucketed; terminal is "" or a single letter (no d5).
        for pos, ch in enumerate(mids):
            offset += _SUFFIX_SPACE + int(ch) * buckets[pos]
        if len(suffix) == 0:
            pass
        elif len(suffix) == 1:
            offset += _ALPHABET.index(suffix) + 1
        else:
            return None
    elif len(mids) == 4:
        # d2 d3 d4 bucketed; the 5th digit is the terminal token, no letters.
        if suffix:
            return None
        for pos in range(3):
            offset += _SUFFIX_SPACE + int(mids[pos]) * buckets[pos]
        offset += 25 + int(mids[3])
    else:
        return None

    if offset < 0 or offset > _MAX_OFFSET:
        return None
    return f"{offset + _ICAO_US_BASE:06x}"
