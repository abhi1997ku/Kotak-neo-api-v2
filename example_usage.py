from auth import login_session
from config import get_client
from market_data import get_positions, get_holdings
from orders import order_report


if __name__ == "__main__":
    session = login_session(get_client())
    client = session["client"]

    print("LOGIN_RESPONSE:")
    print(session["login"])
    print("\nVALIDATE_RESPONSE:")
    print(session["validate"])

    print("\nPOSITIONS:")
    print(get_positions(client))

    print("\nHOLDINGS:")
    print(get_holdings(client))

    print("\nORDER_REPORT:")
    print(order_report(client))
