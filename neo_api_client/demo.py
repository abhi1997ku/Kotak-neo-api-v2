# from neo_api_client import NeoAPI
import io
import os
import neo_api_client
import threading


def on_message(message):
    print('[Res]: ', message)


def on_error(message):
    result = message
    print('[OnError]: ', result)


# This legacy SDK example reads credentials from the environment.
# Use the desktop terminal or example_usage.py for the current TOTP login flow.
client = neo_api_client.NeoAPI(
    consumer_key=os.environ["KOTAK_CONSUMER_KEY"],
    environment=os.getenv("KOTAK_ENVIRONMENT", "prod"),
)
client.on_message = on_message
client.on_error = on_error
# client = neo_api_client.NeoAPI(access_token='',
#                                environment='prod', on_message=on_message,
#                                on_error=on_error)
client.login(mobilenumber="", password="CCCCCCC")
a = client.session_2fa("OTP")
print(a)
# print(client.search_scrip(exchange_segment="nse_fo", symbol="ICICI", expiry="", option_type="CE",strike_price=">505"))
print(client.search_scrip(exchange_segment="nse_fo", symbol="BANKNIFTY", expiry="", option_type="CE",strike_price="45000"))
# client.subscribe_to_orderfeed()
# print(type(a))
