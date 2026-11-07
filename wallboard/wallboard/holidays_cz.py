"""Czech public holidays, with Czech and English names."""

from datetime import date, timedelta

_FIXED_HOLIDAYS = [
    (1, 1, "Den obnovy samostatného českého státu, Nový rok",
     "Restoration Day of the Czech State, New Year's Day"),
    (5, 1, "Svátek práce", "Labour Day"),
    (5, 8, "Den vítězství", "Liberation Day"),
    (7, 5, "Den slovanských věrozvěstů Cyrila a Metoděje",
     "Saints Cyril and Methodius Day"),
    (7, 6, "Den upálení mistra Jana Husa", "Jan Hus Day"),
    (9, 28, "Den české státnosti", "Statehood Day"),
    (10, 28, "Den vzniku samostatného československého státu",
     "Independence Day"),
    (11, 17, "Den boje za svobodu a demokracii", "Freedom and Democracy Day"),
    (12, 24, "Štědrý den", "Christmas Eve"),
    (12, 25, "1. svátek vánoční", "Christmas Day"),
    (12, 26, "2. svátek vánoční", "St Stephen's Day"),
]


def easter_sunday(year: int) -> date:
    """Easter Sunday in the Gregorian calendar (anonymous Gregorian algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def czech_holidays(year: int) -> dict[date, dict[str, str]]:
    """Return Czech public holidays for `year`, sorted by date.

    Each value is a dict with "cs" and "en" names.
    """
    holidays: dict[date, dict[str, str]] = {}
    for month, day, cs, en in _FIXED_HOLIDAYS:
        holidays[date(year, month, day)] = {"cs": cs, "en": en}

    easter = easter_sunday(year)
    holidays[easter - timedelta(days=2)] = {"cs": "Velký pátek", "en": "Good Friday"}
    holidays[easter + timedelta(days=1)] = {
        "cs": "Velikonoční pondělí",
        "en": "Easter Monday",
    }

    return dict(sorted(holidays.items()))
