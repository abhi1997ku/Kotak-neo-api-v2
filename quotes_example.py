from auth import login_session
from config import get_client
from market_data import search_scrip, get_quotes


if __name__ == "__main__":
    session = login_session(get_client())
    client = session["client"]

    # Example: search by symbol in NSE F&O
    search_resp = search_scrip(
        client=client,
        exchange_segment="nse_fo",
        symbol="BANKNIFTY",
        expiry="",
        option_type="CE",
        strike_price="45000",
    )
    print("SEARCH_SCRIP:")
    print(search_resp)

    # Example: quote call using instrument token + exchange segment
    # Replace with real values from search results before running live.
    quote_tokens = [
        {"instrument_token": "26009", "exchange_segment": "nse_cm"},
        {"instrument_token": "3435", "exchange_segment": "nse_cm"},
    ]

    print("\nQUOTES:")
    print(get_quotes(client, quote_tokens, quote_type="ltp"))
