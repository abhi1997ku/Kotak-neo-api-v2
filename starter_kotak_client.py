import os
from dotenv import load_dotenv

from neo_api_client import NeoAPI


load_dotenv()


def get_client():
    return NeoAPI(
        environment=os.getenv("KOTAK_ENVIRONMENT", "prod").lower(),
        access_token=None,
        neo_fin_key=None,
        consumer_key=os.getenv("KOTAK_CONSUMER_KEY"),
    )


def main():
    consumer_key = os.getenv("KOTAK_CONSUMER_KEY")
    mobile = os.getenv("KOTAK_MOBILE")
    ucc = os.getenv("KOTAK_UCC")
    mpin = os.getenv("KOTAK_MPIN")
    totp = os.getenv("KOTAK_TOTP")

    if not consumer_key:
        raise ValueError("Set KOTAK_CONSUMER_KEY in your .env file before running this script.")
    if not mobile or not ucc or not mpin:
        raise ValueError("Set KOTAK_MOBILE, KOTAK_UCC, and KOTAK_MPIN in your .env file before running this script.")
    if not totp:
        raise ValueError("Set KOTAK_TOTP in your .env file before running this script.")

    client = get_client()

    print("Step 1: TOTP login...")
    login_resp = client.totp_login(
        mobile_number=mobile,
        ucc=ucc,
        totp=totp,
    )
    print(login_resp)

    print("\nStep 2: TOTP validate / generate edit token...")
    validate_resp = client.totp_validate(mpin=mpin)
    print(validate_resp)

    print("\nStep 3: Fetch positions...")
    positions = client.positions()
    print(positions)

    print("\nStep 4: Fetch holdings...")
    holdings = client.holdings()
    print(holdings)


if __name__ == "__main__":
    main()
