from auth import login_session
from config import get_client
from orders import place_order_template


if __name__ == "__main__":
    session = login_session(get_client())
    client = session["client"]

    # Replace the values below with a valid live instrument before placing a real order.
    order = place_order_template(
        client,
        exchange_segment="nse_cm",
        product="CNC",
        price="100",
        order_type="L",
        quantity="1",
        validity="DAY",
        trading_symbol="RELIANCE",
        transaction_type="B",
        amo="NO",
        disclosed_quantity="0",
        market_protection="0",
        pf="N",
        trigger_price="0",
        tag="kotak-demo-order",
    )

    print(order)
