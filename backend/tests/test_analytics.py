from datetime import UTC, date, datetime, time, timedelta

import pytest

from backend.app.analytics import Dataset, Vintage, decision_cutoff, delivery_grid, empirical_range, get_as_of_forecast, metrics, quantile
from backend.app.services import evolution


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_as_of_selection_includes_cutoff_but_excludes_later_and_orders_input():
    target = int(dt("2026-01-15T18:00Z").timestamp())
    rows = [Vintage(target, dt("2026-01-14T10:30Z").timestamp(), 6500),
            Vintage(target, dt("2026-01-14T08:30Z").timestamp(), 7000),
            Vintage(target, dt("2026-01-14T09:20Z").timestamp(), 6900)]
    cutoff = dt("2026-01-14T09:20Z")
    data = Dataset([], rows)
    assert get_as_of_forecast(rows, cutoff).generation_mw == 6900
    assert data.forecast(target, cutoff).generation_mw == 6900
    assert data.forecast(target, dt("2026-01-14T07:30Z")) is None
    assert data.forecast(target + 3600, cutoff) is None


def test_naive_cutoff_rejected():
    with pytest.raises(ValueError, match="Timezone-aware"):
        Dataset([], []).forecast(0, datetime(2026, 1, 1))


def test_error_metrics_and_quantile_definitions():
    result = metrics([-1000, 3000])
    assert result["bias_mw"] == 1000
    assert result["mae_mw"] == 2000
    assert result["rmse_mw"] == pytest.approx(2236.0679775)
    assert result["p90_absolute_error_mw"] == 2800
    assert quantile([-1000, 0, 1000], .1) == -800
    assert metrics([])["mae_mw"] is None


def test_cutoff_uses_previous_london_calendar_day_across_dst():
    assert decision_cutoff(date(2026, 3, 30), time(9, 20)) == dt("2026-03-29T08:20Z")
    assert len(delivery_grid(date(2026, 3, 29), 30)) == 46
    assert len(delivery_grid(date(2026, 10, 25), 30)) == 50


def history_dataset(late_actual=False):
    forecasts, actuals = [], []
    for day_number in range(2, 12):
        day = date(2026, 1, day_number)
        cutoff = decision_cutoff(day, time(9, 20))
        for target in delivery_grid(day):
            forecasts.append(Vintage(target, cutoff.timestamp() - 3600, 7000))
            actuals.append(Vintage(target, target + 1800, 6000))
            # A later correction must never replace the version visible at replay time.
            actuals.append(Vintage(target, dt("2026-02-02T00:00Z").timestamp(), 10000))
    if late_actual:
        actuals = [row for row in actuals if row.delivery_ts != delivery_grid(date(2026, 1, 2))[0]]
    return Dataset(actuals, forecasts)


def test_calibration_excludes_future_actuals_and_corrections():
    data = history_dataset()
    calibration = data.calibration(dt("2026-01-09T09:20Z"), time(9, 20))
    assert calibration["available"]
    assert calibration["sample_count"] == 168
    assert calibration["p10_error_mw"] == -1000
    assert calibration["date_to"] == "2026-01-08"


def test_incomplete_history_day_not_used_and_cold_start_exposed():
    data = history_dataset(late_actual=True)
    calibration = data.calibration(dt("2026-01-09T09:20Z"), time(9, 20))
    assert calibration["day_count"] == 6
    assert not calibration["available"]
    assert calibration["p10_error_mw"] is None


def test_range_floor_and_downside_direction():
    bounds = empirical_range(500, {"available": True, "p10_error_mw": -1000, "p50_error_mw": -100, "p90_error_mw": 800})
    assert bounds == {"lower_mw": 0, "median_mw": 400, "upper_mw": 1300, "downside_mw": 500}


def test_replay_hides_future_payload_and_actuals():
    data = history_dataset()
    target = dt("2026-01-10T18:00Z")
    data.forecasts[int(target.timestamp())].append(Vintage(int(target.timestamp()), dt("2026-01-10T12:00Z").timestamp(), 5000))
    hidden = evolution(data, target, time(9, 20), False)
    assert hidden["actual_mw"] is None
    assert hidden["error_mw"] is None
    assert all(row["eligible"] for row in hidden["versions"])
    revealed = evolution(data, target, time(9, 20), True)
    assert revealed["actual_mw"] == 10000
    assert any(not row["eligible"] for row in revealed["versions"])
    assert revealed["forecast_mw"] == hidden["forecast_mw"] == 7000
