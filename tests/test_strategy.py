from __future__ import annotations

from ibsim.risk import fractional_kelly, risk_metrics
from ibsim.strategy import dual_moving_average_backtest


def test_fractional_kelly_matches_course_example() -> None:
    result = fractional_kelly(0.55, 1.0, 1.0)
    assert round(result.full_kelly_fraction, 6) == 0.1


def test_risk_metrics_focus_on_losses_and_drawdown() -> None:
    result = risk_metrics([0.05, -0.10, 0.02, -0.03])
    assert result.loss_probability == 0.5
    assert result.expected_loss < 0
    assert result.max_drawdown > 0


def test_dual_sma_cleans_nans_sorts_and_shifts_signal() -> None:
    rows = [
        {"date": "2026-01-03", "close": 102},
        {"date": "2026-01-01", "close": 100},
        {"date": "2026-01-02", "close": ""},
        {"date": "2026-01-04", "close": 103},
        {"date": "2026-01-05", "close": 104},
        {"date": "2026-01-06", "close": 105},
        {"date": "2026-01-07", "close": 106},
    ]
    result = dual_moving_average_backtest(rows, short_window=2, long_window=3)
    assert result.observations == 6
    assert result.trades >= 1
    assert result.warnings
