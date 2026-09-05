from backend.routes.auth import auth_error_status, normalize_auth_error


def test_invalid_totp_status_is_400():
    assert auth_error_status("Invalid TOTP") == 400
    assert auth_error_status("Broker rejected the login: Invalid TOTP / MPIN") == 400


def test_invalid_totp_message_becomes_user_friendly():
    assert normalize_auth_error("Invalid TOTP") == (
        "Broker rejected the login: Invalid TOTP / MPIN. "
        "Generate a fresh TOTP from your Kotak app and try again."
    )
