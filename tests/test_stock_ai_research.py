from screener.stock_ai_research import eligible_training, validation_objective, make_model, improvement_interval


def test_training_purges_unfinished_and_boundary_labels_across_symbols():
    records = [
        {"day": "2025-01-01", "label_end": "2025-01-10", "y": 1},
        {"day": "2025-01-01", "label_end": "2025-02-01", "y": -1},
        {"day": "2025-01-31", "label_end": "2025-02-14", "y": 3},
        {"day": "2025-01-01", "label_end": None, "y": None},
    ]
    assert eligible_training(records, "2025-02-01") == [records[0]]


def test_selection_rejects_tiny_sample_even_with_extreme_return():
    assert validation_objective({"closed_trades": 2, "return_pct": 100, "max_drawdown_pct": 0}) < 0
    assert validation_objective({"closed_trades": 15, "return_pct": 5, "max_drawdown_pct": 2}) == 4


def test_no_random_early_stopping_split():
    assert make_model("hist_gradient_boosting").early_stopping is False
    assert make_model("extra_trees").random_state == 42


def test_identical_portfolios_have_zero_improvement_interval():
    portfolio = {"equity_curve": [{"equity": 100000+i*100} for i in range(30)]}
    interval = improvement_interval(portfolio, portfolio)
    assert interval["lower_pct_points"] == 0
    assert interval["upper_pct_points"] == 0
