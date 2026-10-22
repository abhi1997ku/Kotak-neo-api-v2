import pytest

from backend.paper_trading import BankNiftySignalEngine, OneMinuteCandle, PaperShortOptionRules


def test_paper_trade_uses_requested_stop_target_and_trailing_levels():
    rules = PaperShortOptionRules()
    trade = rules.open_trade("BANKNIFTY PE", quantity=rules.quantity_for_lot_size(30), entry_price=100)

    assert trade.quantity == 90
    assert (trade.stop_price, trade.target_price) == (70, 190)
    assert rules.on_tick(trade, 130).stop_price == 110
    assert rules.on_tick(trade, 160).stop_price == 130
    assert rules.on_tick(trade, 180).stop_price == 150


def test_paper_trade_closes_at_target_and_trailing_stop():
    rules = PaperShortOptionRules()
    target_trade = rules.open_trade("BANKNIFTY PE", quantity=30, entry_price=100)
    assert rules.on_tick(target_trade, 190).exit_reason == "target"

    stopped_trade = rules.open_trade("BANKNIFTY PE", quantity=30, entry_price=100)
    rules.on_tick(stopped_trade, 160)
    assert rules.on_tick(stopped_trade, 130).exit_reason == "trailing_stop"


@pytest.mark.parametrize("step,direction", [(10, "long"), (-10, "short")])
def test_initial_trend_signals_once_after_25_completed_candles(step, direction):
    engine = BankNiftySignalEngine()
    for minute in range(25):
        assert engine.on_tick(55000 + step * minute, 1000 + minute * 100, minute * 60) is None
    assert engine.on_tick(55000 + step * 25, 3500, 25 * 60) == direction
    for minute in range(26, 100):
        assert engine.on_tick(55000 + step * minute, 1000 + minute * 100, minute * 60) is None


@pytest.mark.parametrize("volume", [0, 100])
def test_initial_neutral_or_missing_volume_does_not_signal(volume):
    engine = BankNiftySignalEngine()
    for minute in range(30):
        assert engine.on_tick(55000, 1000 + minute * volume, minute * 60) is None


@pytest.mark.parametrize("allow_initial_signal", [True, False])
def test_restored_trend_requires_fresh_close_and_respects_initial_entry_flag(allow_initial_signal):
    engine = BankNiftySignalEngine()
    candles = [OneMinuteCandle(i, 55000-i*10, 55000-i*10, 55000-i*10, 55000-i*10, 100) for i in range(30)]
    engine.load_completed_candles(candles, allow_initial_signal=allow_initial_signal)
    assert engine.on_tick(54700, 4000, 30*60) is None
    assert engine.on_tick(54695, 4050, 30*60+30) is None
    assert engine.on_tick(54690, 4100, 31*60) == ("short" if allow_initial_signal else None)
    assert engine.on_tick(54680, 4200, 32*60) is None
