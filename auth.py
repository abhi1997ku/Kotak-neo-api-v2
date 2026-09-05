from config import get_client, get_env


def login_session(client=None):
    if client is None:
        client = get_client()

    mobile = get_env("KOTAK_MOBILE")
    ucc = get_env("KOTAK_UCC")
    totp = get_env("KOTAK_TOTP")
    mpin = get_env("KOTAK_MPIN")

    if not mobile or not ucc or not totp:
        raise ValueError("KOTAK_MOBILE, KOTAK_UCC, and KOTAK_TOTP must be set in .env")
    if not mpin:
        raise ValueError("KOTAK_MPIN must be set in .env")

    login_resp = client.totp_login(mobile_number=mobile, ucc=ucc, totp=totp)
    validate_resp = client.totp_validate(mpin=mpin)

    return {
        "client": client,
        "login": login_resp,
        "validate": validate_resp,
    }


def ensure_authenticated():
    session = login_session()
    return session["client"]
