"""
Session management for authenticated Kotak client.
Stores tokens in memory for single-user personal app.
"""

import asyncio
import logging
import threading
from datetime import datetime, time as clock_time
from typing import Optional
from zoneinfo import ZoneInfo

from backend.kotak_client import KotakClient, KotakClientError, KotakSession, KotakSessionExpired

logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL_SECONDS = 180
KEEPALIVE_TIMEOUT_SECONDS = 20
KEEPALIVE_START = clock_time(8, 45)
KEEPALIVE_END = clock_time(16, 0)
INDIA_TZ = ZoneInfo("Asia/Kolkata")


class SessionManager:
    """Simple in-memory session storage for personal single-user app."""

    _instance: Optional["SessionManager"] = None

    def __init__(self):
        self.session: Optional[KotakSession] = None
        self.client: Optional[KotakClient] = None
        self._lock = threading.RLock()
        self.session_health = "not_authenticated"
        self.last_session_check_at: Optional[str] = None
        self.last_session_check_error: Optional[str] = None

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

        client = KotakClient()
        try:
            client.login(totp=totp.strip())
            client.login_with_mpin(mpin=mpin.strip())
        except Exception:
            self.logout()
            raise

        with self._lock:
            self.client = client
            self.session = client.session
            self.session_health = "active"
            self.last_session_check_at = None
            self.last_session_check_error = None

        return {
            "status": "success",
            "message": "Successfully authenticated",
        }

    def get_client(self) -> Optional[KotakClient]:
        """Get authenticated client if session exists."""
        with self._lock:
            if self.client and self.session and self.session.trade_token:
                return self.client
            return None

    def logout(self):
        """Clear session."""
        with self._lock:
            self.session = None
            self.client = None
            self.session_health = "not_authenticated"
            self.last_session_check_at = None
            self.last_session_check_error = None

    def _invalidate_if_current(self, client: KotakClient, reason: str) -> None:
        """Drop only the session that Kotak explicitly rejected."""
        with self._lock:
            if self.client is not client:
                return
            self.session = None
            self.client = None
            self.session_health = "expired"
            self.last_session_check_at = datetime.now(INDIA_TZ).isoformat(timespec="seconds")
            self.last_session_check_error = reason[:240]

    def _record_keepalive(self, client: KotakClient, error: Optional[str] = None) -> None:
        with self._lock:
            if self.client is not client:
                return
            self.session_health = "degraded" if error else "active"
            self.last_session_check_at = datetime.now(INDIA_TZ).isoformat(timespec="seconds")
            self.last_session_check_error = error[:240] if error else None

    def status(self) -> dict:
        """Return local auth state and the last broker keepalive result."""
        with self._lock:
            authenticated = bool(self.session and self.session.trade_token and self.session.edit_sid and self.client)
            health = self.session_health if authenticated or self.session_health == "expired" else "not_authenticated"
            if health == "expired":
                message = "Kotak rejected the session. Please log in again."
            elif health == "degraded":
                message = "Kotak session check failed temporarily; the current login is being retained."
            elif authenticated:
                message = "Session is active"
            else:
                message = "Please login with your TOTP and MPIN"
            return {
                "status": "authenticated" if authenticated else "not_authenticated",
                "authenticated": authenticated,
                "session_health": health,
                "last_session_check_at": self.last_session_check_at,
                "message": message,
            }

    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        with self._lock:
            return (
                self.session is not None
                and self.session.trade_token is not None
                and self.session.edit_sid is not None
                and self.client is not None
            )

    async def keepalive_loop(self) -> None:
        """Keep an idle broker session active during the trading day.

        The read-only limits request uses the existing trade session. Ordinary
        network/broker failures are recorded but do not discard that session;
        only an explicit Kotak auth rejection invalidates it.
        """
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
            now = datetime.now(INDIA_TZ)
            if now.weekday() >= 5 or not (KEEPALIVE_START <= now.time() <= KEEPALIVE_END):
                continue

            client = self.get_client()
            if not client:
                continue

            try:
                await asyncio.wait_for(
                    asyncio.to_thread(client.refresh_session_if_needed),
                    timeout=KEEPALIVE_TIMEOUT_SECONDS,
                )
                self._record_keepalive(client)
            except asyncio.CancelledError:
                raise
            except KotakSessionExpired as exc:
                logger.warning("Kotak rejected the active session during keepalive: %s", str(exc))
                self._invalidate_if_current(client, str(exc))
            except (asyncio.TimeoutError, KotakClientError) as exc:
                logger.warning("Kotak session keepalive did not complete: %s", str(exc)[:240])
                self._record_keepalive(client, str(exc))
            except Exception as exc:
                logger.warning("Unexpected Kotak session keepalive failure: %s", type(exc).__name__)
                self._record_keepalive(client, type(exc).__name__)


def get_session_manager() -> SessionManager:
    """Get the global session manager."""
    return SessionManager.get_instance()
