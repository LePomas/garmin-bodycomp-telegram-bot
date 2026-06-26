"""Self-checks for the pure trend math in llmfeedback.

Run directly (no pytest needed):  python test_llmfeedback.py
Or under pytest:                   pytest test_llmfeedback.py
"""
import llmfeedback as lf


class FakeApi:
    """Stands in for garminconnect.Garmin; returns canned dateWeightList rows."""

    def __init__(self, rows):
        self._rows = rows

    def get_body_composition(self, start, end):
        return {"dateWeightList": self._rows}


def test_normalize_grams_to_kg():
    row = {"calendarDate": "2026-06-01", "weight": 80000, "bodyFat": 18.0, "muscleMass": 34000}
    assert lf.normalize_entry(row) == {
        "date": "2026-06-01",
        "weight_kg": 80.0,
        "body_fat_percent": 18.0,
        "muscle_mass_kg": 34.0,
    }
    # missing values normalize to None instead of crashing
    assert lf.normalize_entry({"calendarDate": "2026-06-01"}) == {
        "date": "2026-06-01",
        "weight_kg": None,
        "body_fat_percent": None,
        "muscle_mass_kg": None,
    }


def test_fetch_sorts_oldest_first_and_drops_weightless():
    rows = [
        {"calendarDate": "2026-06-10", "weight": 79000},
        {"calendarDate": "2026-06-01", "weight": 80000},
        {"calendarDate": "2026-06-05"},  # no weight -> dropped
    ]
    series = lf.fetch_body_composition_series(FakeApi(rows))
    assert [r["date"] for r in series] == ["2026-06-01", "2026-06-10"]


def test_fetch_empty_returns_none():
    assert lf.fetch_body_composition_series(FakeApi([])) is None


def test_trend_uses_least_squares_over_window():
    # Perfectly linear: -0.1 kg/day, 80.0 -> 77.0 across 30 days.
    rows = [
        {"calendarDate": "2026-06-01", "weight": 80000, "bodyFat": 20.0},
        {"calendarDate": "2026-06-11", "weight": 79000, "bodyFat": 19.0},
        {"calendarDate": "2026-06-21", "weight": 78000, "bodyFat": 18.0},
        {"calendarDate": "2026-07-01", "weight": 77000, "bodyFat": 17.0},
    ]
    trends = lf.compute_trends(lf.fetch_body_composition_series(FakeApi(rows)))
    w = trends["weight_kg"]
    assert w["current"] == 77.0
    assert w["span_days"] == 30
    assert w["change"] == -3.0  # slope -0.1 kg/day * 30 days
    assert trends["body_fat_percent"]["change"] == -3.0
    assert trends["muscle_mass_kg"] is None  # not present in any row


def test_trend_robust_to_a_noisy_middle_reading():
    # One bad middle reading should not flip a clear downward trend positive.
    rows = [
        {"calendarDate": "2026-06-01", "weight": 80000},
        {"calendarDate": "2026-06-15", "weight": 85000},  # noise spike
        {"calendarDate": "2026-07-01", "weight": 78000},
    ]
    w = lf.compute_trends(lf.fetch_body_composition_series(FakeApi(rows)))["weight_kg"]
    assert w["change"] < 0  # least-squares still trends down despite the spike


def test_single_point_has_no_change():
    rows = [{"calendarDate": "2026-06-01", "weight": 80000}]
    w = lf.compute_trends(lf.fetch_body_composition_series(FakeApi(rows)))["weight_kg"]
    assert w["current"] == 80.0
    assert w["change"] is None


def test_describe_trends_text():
    trends = {
        "weight_kg": {"current": 77.0, "change": -3.0, "span_days": 30, "n": 4},
        "body_fat_percent": {"current": 17.0, "change": -3.0, "span_days": 30, "n": 4},
        "muscle_mass_kg": None,
    }
    lines = lf.describe_trends(trends)
    assert any("Weight -3.00 kg over 30 days" in line for line in lines)
    assert any("Body fat -3.00% over 30 days" in line for line in lines)
    assert all("Muscle" not in line for line in lines)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"all {len(tests)} passed")


if __name__ == "__main__":
    _run()
