import os
from dotenv import load_dotenv


load_dotenv()


def get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) and value.strip() else default


def get_client():
    from neo_api_client import NeoAPI

    environment = get_env("KOTAK_ENVIRONMENT", "prod")
    consumer_key = get_env("KOTAK_CONSUMER_KEY")

    if not consumer_key:
        raise ValueError("KOTAK_CONSUMER_KEY is missing. Set it in the .env file.")

    return NeoAPI(
        environment=environment,
        access_token=None,
        neo_fin_key=None,
        consumer_key=consumer_key,
    )
