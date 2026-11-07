from datetime import date

from wallboard.holidays_cz import czech_holidays


def test_2026_count_13():
    holidays = czech_holidays(2026)
    assert len(holidays) == 13


def test_easter_2026():
    holidays = czech_holidays(2026)
    assert holidays[date(2026, 4, 3)] == {"cs": "Velký pátek", "en": "Good Friday"}
    assert holidays[date(2026, 4, 6)] == {"cs": "Velikonoční pondělí", "en": "Easter Monday"}


def test_easter_2027():
    holidays = czech_holidays(2027)
    assert holidays[date(2027, 3, 26)] == {"cs": "Velký pátek", "en": "Good Friday"}
    assert holidays[date(2027, 3, 29)] == {"cs": "Velikonoční pondělí", "en": "Easter Monday"}


def test_nov_17_names():
    holidays = czech_holidays(2026)
    assert holidays[date(2026, 11, 17)] == {
        "cs": "Den boje za svobodu a demokracii",
        "en": "Freedom and Democracy Day",
    }
