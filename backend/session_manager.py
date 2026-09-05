"""
Session management for authenticated Kotak client.
Stores tokens in memory for single-user personal app.
"""

from typing import Optional

from backend.kotak_client import KotakClient, KotakSession


class SessionManager:
    """Simple in-memory session storage for personal single-user app."""

    _instance: Optional["SessionManager"] = None

    def __init__(self):
        self.session: Optional[KotakSession] = None
        self.client: Optional[KotakClient] = None

    @classmethod
    def get_instance(cls) -> "SessionManager":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = SessionManager()
        return cls._instance

    def login(self, totp: str, mpin: str) -> dict:
        """Authenticate with TOTP and MPIN for a live broker session."""
        if not totp:
            raise ValueError("TOTP is required.")
        if not mpin:
            raise ValueError("MPIN is required.")

        self.client = KotakClient()

        self.client.login(totp=totp)
        self.client.login_with_mpin(mpin=mpin)

        self.session = self.client.session

        return {
            "status": "success",
            "message": "Successfully authenticated",
            "view_token": self.session.view_token,
            "trade_token": self.session.trade_token,
            "sid": self.session.sid,
            "edit_sid": self.session.edit_sid,
        }

    def get_client(self) -> Optional[KotakClient]:
        """Get authenticated client if session exists."""
        if self.client and self.session and self.session.trade_token:
            return self.client
        return None

    def logout(self):
        """Clear session."""
        self.session = None
        self.client = None

    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return (
            self.session is not None
            and self.session.trade_token is not None
            and self.session.edit_sid is not None
        )


def get_session_manager() -> SessionManager:
    """Get the global session manager."""
    return SessionManager.get_instance()
