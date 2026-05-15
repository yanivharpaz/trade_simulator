from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ibsim.models import ClockMode


class SimClock:
    """Deterministic clock supporting wall, paused/step, and compressed modes."""

    def __init__(
        self,
        *,
        mode: ClockMode = ClockMode.WALL,
        start: datetime | None = None,
        speed: float = 1.0,
    ) -> None:
        self.mode = mode
        self.speed = max(speed, 0.0)
        self._real_anchor = self._utc_now()
        self._sim_anchor = start or self._real_anchor
        self._paused_at = self._sim_anchor

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def now(self) -> datetime:
        if self.mode == ClockMode.PAUSED:
            return self._paused_at
        if self.mode == ClockMode.COMPRESSED:
            elapsed = self._utc_now() - self._real_anchor
            return self._sim_anchor + timedelta(seconds=elapsed.total_seconds() * self.speed)
        return self._utc_now()

    def pause(self) -> datetime:
        self._paused_at = self.now()
        self.mode = ClockMode.PAUSED
        return self._paused_at

    def resume_wall(self) -> None:
        self.mode = ClockMode.WALL
        self._real_anchor = self._utc_now()
        self._sim_anchor = self._real_anchor

    def resume_compressed(self, *, speed: float = 60.0) -> None:
        self._sim_anchor = self.now()
        self._real_anchor = self._utc_now()
        self.speed = max(speed, 0.0)
        self.mode = ClockMode.COMPRESSED

    def step(self, delta: timedelta) -> datetime:
        if self.mode != ClockMode.PAUSED:
            self.pause()
        self._paused_at = self._paused_at + delta
        return self._paused_at
