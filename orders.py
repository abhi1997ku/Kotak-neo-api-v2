def place_order_template(client, **kwargs):
    """Convenience helper for order placement.

    Uses the SDK defaults from README but keeps the call easy to customize.
    """
    payload = {
        "exchange_segment": kwargs.get("exchange_segment"),
        "product": kwargs.get("product"),
        "price": kwargs.get("price", "0"),
        "order_type": kwargs.get("order_type"),
        "quantity": kwargs.get("quantity"),
        "validity": kwargs.get("validity", "DAY"),
        "trading_symbol": kwargs.get("trading_symbol"),
        "transaction_type": kwargs.get("transaction_type"),
        "amo": kwargs.get("amo", "NO"),
        "disclosed_quantity": kwargs.get("disclosed_quantity", "0"),
        "market_protection": kwargs.get("market_protection", "0"),
        "pf": kwargs.get("pf", "N"),
        "trigger_price": kwargs.get("trigger_price", "0"),
        "tag": kwargs.get("tag"),
        "scrip_token": kwargs.get("scrip_token"),
        "square_off_type": kwargs.get("square_off_type"),
        "stop_loss_type": kwargs.get("stop_loss_type"),
        "stop_loss_value": kwargs.get("stop_loss_value"),
        "square_off_value": kwargs.get("square_off_value"),
        "last_traded_price": kwargs.get("last_traded_price"),
        "trailing_stop_loss": kwargs.get("trailing_stop_loss"),
        "trailing_sl_value": kwargs.get("trailing_sl_value"),
    }
    return client.place_order(**payload)


def cancel_order(client, order_id, amo="NO", is_verify=False):
    return client.cancel_order(order_id=order_id, amo=amo, isVerify=is_verify)


def order_report(client):
    return client.order_report()


def order_history(client, order_id):
    return client.order_history(order_id=order_id)


def trade_report(client, order_id=None):
    if order_id:
        return client.trade_report(order_id=order_id)
    return client.trade_report()
