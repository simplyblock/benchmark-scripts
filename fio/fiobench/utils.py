import re


def parse_size(size_string: str, mode: str = 'si/iec', unit: str = '', strict: bool = False) -> int:
    """Parse the given data size

    If passed and not explicitly given, 'unit' will be assumed.
    Mode can be either 'si/iec' to parse decimal (SI) and binary (IEC) units, or
    'jedec' for binary only units. If `strict`, parsing will be case-sensitive and
    expect the 'B' suffix.
    """
    if not unit:
        try:
            x = int(size_string)
            return x
        except Exception:
            pass

    try:
        m = re.match(r'^(?P<size_in_unit>\d+) ?(?P<unit>\w+)?$', size_string.strip())
        if m is None:
            raise ValueError(f"Invalid size: {size_string}")

        size_in_unit = int(m.group('size_in_unit'))
        unit = m.group('unit') if m.group('unit') else unit
        base, exponent = _parse_unit(unit, mode, strict=strict)
        return size_in_unit * (base ** exponent)
    except ValueError:
        return -1


def _parse_unit(unit: str, mode: str = 'si/iec', strict: bool = True) -> tuple[int, int]:
    """Parse the given unit, returning the associated base and exponent

    Mode can be either 'si/iec' to parse decimal (SI) and binary (IEC) units, or
    'jedec' for binary only units. If `strict`, parsing will be case-sensitive and
    expect the 'B' suffix.
    """
    regexes = {
        'si/iec': r'^((?P<prefix>[kKMGTPEZ])(?P<binary>i)?)?' + ('B$' if strict else 'B?$'),
        'jedec': r'^(?P<prefix>[KMGTPEZ])?' + ('B$' if strict else 'B?$'),
    }

    m = re.match(regexes[mode], unit, flags=re.IGNORECASE if not strict else 0)
    if m is None:
        raise ValueError("Invalid unit")

    binary = (mode == 'jedec') or (m.group('binary') is not None)
    prefix = m.group('prefix') or ''

    if strict and (binary and (prefix == 'k')) or ((not binary) and (prefix == 'K')):
        raise ValueError("Invalid unit")

    exponent_multipliers = ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']
    return (
        2 if binary else 10,
        (10 if binary else 3) * exponent_multipliers.index(prefix.upper())
    )
