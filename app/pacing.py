"""Frame pacing for the active NeuralScreen pipeline.

The limiter schedules frame *starts* on a monotonic timeline.  It never tries
to catch up after a slow frame: bursts are worse than a temporarily lower
rate, especially when Frame Generation is consuming the output cadence.
"""
from __future__ import annotations

import time
from typing import Callable


class FramePacer:
    """A drift-resistant, dynamically reconfigurable frame limiter."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._deadline: float | None = None
        self._fps = 0.0

    def reset(self) -> None:
        self._deadline = None
        self._fps = 0.0

    def wait(self, fps: float, frame_started: float) -> float:
        """Sleep until this frame's deadline and return requested sleep time.

        ``fps <= 0`` means unlimited.  ``frame_started`` is captured at the
        beginning of the main-loop iteration, which makes the cap cover the
        whole capture/evaluate/present cycle instead of adding an interval on
        top of the processing time.
        """
        try:
            fps = float(fps)
        except (TypeError, ValueError, OverflowError):
            fps = 0.0
        if fps <= 0.0 or fps != fps or fps == float("inf"):
            self.reset()
            return 0.0

        interval = 1.0 / fps
        frame_started = float(frame_started)
        if self._deadline is None or abs(self._fps - fps) > 1e-9:
            self._fps = fps
            self._deadline = frame_started + interval
        else:
            # A slow frame must not be followed by a catch-up burst.  Keep the
            # stable timeline while it is ahead, otherwise restart it from the
            # actual frame start.
            self._deadline = max(self._deadline, frame_started + interval)

        now = self._clock()
        delay = max(0.0, self._deadline - now)
        if delay > 0.0:
            self._sleep(delay)
        self._deadline += interval
        return delay


class FrameRateMeter:
    """Rolling rate of completed work, excluding idle/bypass replies."""

    def __init__(self, window: float = 2.0) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        self.window = float(window)
        self._completed: list[float] = []

    def _prune(self, now: float) -> None:
        cutoff = float(now) - self.window
        first = 0
        while first < len(self._completed) and self._completed[first] < cutoff:
            first += 1
        if first:
            del self._completed[:first]

    def record(self, completed_at: float) -> None:
        completed_at = float(completed_at)
        self._completed.append(completed_at)
        self._prune(completed_at)

    def rate(self, now: float) -> float:
        self._prune(float(now))
        if len(self._completed) < 2:
            return 0.0
        span = self._completed[-1] - self._completed[0]
        return (len(self._completed) - 1) / span if span > 0 else 0.0


__all__ = ["FramePacer", "FrameRateMeter"]
