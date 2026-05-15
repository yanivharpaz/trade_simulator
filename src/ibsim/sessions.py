from __future__ import annotations

from datetime import timedelta

from ibsim.clock import SimClock
from ibsim.events import SQLiteEventStore
from ibsim.models import SessionState


class SessionManager:
    def __init__(self, *, clock: SimClock, events: SQLiteEventStore, timeout: timedelta = timedelta(minutes=5)) -> None:
        self.clock = clock
        self.events = events
        self.timeout = timeout
        self.state = self._new_session()

    def _new_session(self) -> SessionState:
        now = self.clock.now()
        return SessionState(
            username="demo-user",
            authenticated=True,
            connected=True,
            brokerageAuthenticated=True,
            competing=False,
            createdAt=now,
            lastTickleAt=now,
            expiresAt=now + self.timeout,
        )

    def _refresh_expiry(self) -> None:
        now = self.clock.now()
        if now > self.state.expires_at:
            self.state.connected = False
            self.state.brokerage_authenticated = False
        else:
            self.state.connected = True

    def auth_status(self) -> dict[str, object]:
        self._refresh_expiry()
        return {
            "authenticated": self.state.authenticated and self.state.brokerage_authenticated,
            "competing": self.state.competing,
            "connected": self.state.connected,
            "message": "" if self.state.connected else "Brokerage session timed out; call /tickle to re-establish it.",
            "serverInfo": {
                "serverName": "SIM-IB-01",
                "serverVersion": "Build sim-0.1.0",
            },
        }

    def tickle(self) -> dict[str, object]:
        now = self.clock.now()
        if not self.state.authenticated:
            self.state = self._new_session()
        self.state.connected = True
        self.state.brokerage_authenticated = True
        self.state.last_tickle_at = now
        self.state.expires_at = now + self.timeout
        payload = {
            "session": self.state.session_id,
            "ssoExpires": int(self.timeout.total_seconds()),
            "collision": self.state.competing,
            "userId": 123456789,
            "iserver": {"authStatus": self.auth_status()},
        }
        self.events.append("session_tickle", payload, aggregate_id=self.state.session_id, ts=now)
        return payload

    def logout(self) -> dict[str, object]:
        self.state.connected = False
        self.state.authenticated = False
        self.state.brokerage_authenticated = False
        self.events.append("session_closed", {"session": self.state.session_id}, aggregate_id=self.state.session_id, ts=self.clock.now())
        return {"status": "logged out"}

    def require_brokerage(self) -> None:
        self._refresh_expiry()
        if not (self.state.connected and self.state.authenticated and self.state.brokerage_authenticated):
            raise PermissionError("Brokerage session is not authenticated. Call /v1/api/tickle first.")
