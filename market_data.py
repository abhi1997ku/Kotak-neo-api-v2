from config import get_env


def get_positions(client):
    return client.positions()


def get_holdings(client):
    return client.holdings()


def get_limits(client, segment="ALL", exchange="ALL", product="ALL"):
    return client.limits(segment=segment, exchange=exchange, product=product)


def search_scrip(client, exchange_segment, symbol, expiry="", option_type="", strike_price=""):
    return client.search_scrip(
        exchange_segment=exchange_segment,
        symbol=symbol,
        expiry=expiry,
        option_type=option_type,
        strike_price=strike_price,
    )


def get_quotes(client, instrument_tokens, quote_type="ltp"):
    return client.quotes(instrument_tokens=instrument_tokens, quote_type=quote_type)


def get_default_quote_tokens():
    return [
        {"instrument_token": get_env("QUOTE_TOKEN_1", ""), "exchange_segment": get_env("QUOTE_SEGMENT_1", "nse_cm")},
        {"instrument_token": get_env("QUOTE_TOKEN_2", ""), "exchange_segment": get_env("QUOTE_SEGMENT_2", "nse_cm")},
    ]
