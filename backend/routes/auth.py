from fastapi import APIRouter, HTTPException

from backend.kotak_client import KotakClientError
from backend.session_manager import get_session_manager

router = APIRouter(prefix="/auth", tags=["auth"])


def auth_error_status(message: str) -> int:
    """Map broker auth failures to the correct HTTP status."""
    lower = message.lower()
    if any(token in lower for token in ["totp", "mpin", "invalid", "required", "rejected"]):
        return 400
    return 401


def normalize_auth_error(message: str) -> str:
    """Convert raw broker errors into a clearer user-facing message."""
    lower = message.lower()
    if "totp" in lower or "invalid" in lower:
        return "Broker rejected the login: Invalid TOTP / MPIN. Generate a fresh TOTP from your Kotak app and try again."
    if "mpin" in lower:
        return "Broker rejected the login: Invalid TOTP / MPIN. Check the MPIN and generate a fresh TOTP from your Kotak app."
    return message


@router.post("/login")
def login(totp: str | None = None, mpin: str | None = None) -> dict:
    """Two-step authentication: TOTP + MPIN from the user session."""
    try:
        if not totp:
            raise HTTPException(status_code=400, detail="TOTP code is required")
        if not mpin:
            raise HTTPException(status_code=400, detail="MPIN is required")

        session_mgr = get_session_manager()
        return session_mgr.login(totp=totp, mpin=mpin)

    except KotakClientError as e:
        message = normalize_auth_error(str(e))
        raise HTTPException(status_code=auth_error_status(str(e)), detail=message)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")


@router.get("/status")
def status() -> dict:
    """Check authentication status."""
    session_mgr = get_session_manager()

    return {
        "status": "authenticated" if session_mgr.is_authenticated() else "not_authenticated",
        "authenticated": session_mgr.is_authenticated(),
        "message": "Session is active" if session_mgr.is_authenticated() else "Please login with your TOTP and MPIN",
    }


@router.post("/logout")
def logout() -> dict:
    """Clear session."""
    session_mgr = get_session_manager()
    session_mgr.logout()
    return {"status": "logged_out", "message": "Session cleared"}
