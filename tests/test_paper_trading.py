from backend.paper_trading import PaperShortOptionRules


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
    assert rules.on_tick(stopped_trade, 130).exit_reason == "stop_loss"
