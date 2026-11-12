"""Conservative local pump-down ETA from a rolling log-pressure trend.

This is an empirical, local exponential extrapolation, not a chamber model:
changing pump speed, outgassing, leaks, or a pressure floor can change its ETA.
Only sustained, well-fitted decreases produce a value. Invalid/missing readings
must call ``reset`` so a previous pump-down cannot supply a stale estimate.
"""

from collections import deque
from dataclasses import dataclass
import math
from statistics import fmean
from typing import Optional


@dataclass(frozen=True)
class Estimate:
    seconds: float
    target_torr: float
    r_squared: float


def format_eta(seconds: float) -> str:
    """Round up to at most three characters for a two-tile display.

    1..99 minutes are shown as M; longer estimates use H. This deliberately
    avoids seconds and fractional hours. Values over 99 hours do not fit.
    """
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("ETA seconds must be finite and nonnegative")
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 100:
        return f"{minutes}M"
    hours = math.ceil(seconds / 3600)
    if hours > 99:
        raise ValueError("ETA exceeds the 99-hour display limit")
    return f"{hours}H"


def _log_fit(samples):
    """Centered OLS of ln(P) against seconds, returning slope and R squared."""
    origin = samples[0][0]
    times = [timestamp - origin for timestamp, _ in samples]
    logs = [math.log(pressure) for _, pressure in samples]
    mean_time, mean_log = fmean(times), fmean(logs)
    time_variance = sum((value - mean_time) ** 2 for value in times)
    log_variance = sum((value - mean_log) ** 2 for value in logs)
    if time_variance <= 0 or log_variance <= 0:
        return 0.0, 0.0
    covariance = sum((timestamp - mean_time) * (pressure - mean_log)
                     for timestamp, pressure in zip(times, logs))
    slope = covariance / time_variance
    r_squared = min(1.0, max(0.0, covariance ** 2 / (time_variance * log_variance)))
    return slope, r_squared


class PumpdownEstimator:
    """Estimate the time to 1e-7 Torr, then 5e-8 Torr, only while falling.

    Defaults: a 300-second rolling window, 60 seconds and 10 samples of fresh
    history, R² >= 0.9, at least a 2% net fall, and no prediction over 24 hours.
    Every upward sample hides the ETA. More than a 1% jump, three consecutive
    increases, or a sustained recent upward trend discards the old history.
    A gap over 30 seconds or a non-increasing timestamp also resets history.
    All quality thresholds are deterministic; no hardware or wall clock is used.
    """

    def __init__(
        self,
        window_seconds: float = 300.0,
        min_span_seconds: float = 60.0,
        min_samples: int = 10,
        min_r_squared: float = 0.9,
        max_eta_seconds: float = 86400.0,
        max_gap_seconds: float = 30.0,
        min_relative_drop: float = 0.02,
    ) -> None:
        for name, value in (("window_seconds", window_seconds),
                            ("min_span_seconds", min_span_seconds),
                            ("max_eta_seconds", max_eta_seconds),
                            ("max_gap_seconds", max_gap_seconds)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if window_seconds < min_span_seconds:
            raise ValueError("window_seconds must be at least min_span_seconds")
        if not isinstance(min_samples, int) or min_samples < 3:
            raise ValueError("min_samples must be an integer of at least 3")
        if not math.isfinite(min_r_squared) or not 0 <= min_r_squared <= 1:
            raise ValueError("min_r_squared must be between 0 and 1")
        if not math.isfinite(min_relative_drop) or not 0 <= min_relative_drop < 1:
            raise ValueError("min_relative_drop must be between 0 and 1")
        self.window_seconds = float(window_seconds)
        self.min_span_seconds = float(min_span_seconds)
        self.min_samples = min_samples
        self.min_r_squared = float(min_r_squared)
        self.max_eta_seconds = float(max_eta_seconds)
        self.max_gap_seconds = float(max_gap_seconds)
        self.min_relative_drop = float(min_relative_drop)
        self.reset()

    def reset(self) -> None:
        """Discard trend history, for example after a fault or vented status."""
        self._samples = deque()
        self._last_timestamp = None
        self._last_pressure = None
        self._rising_streak = 0
        self.reason = "history cleared; collecting fresh pressure data"

    def _seed(self, timestamp, pressure):
        self.reset()
        self._samples.append((timestamp, pressure))
        self._last_timestamp = timestamp
        self._last_pressure = pressure

    def update(self, timestamp_monotonic: float, pressure_torr: float) -> Optional[Estimate]:
        """Accept one valid reading, returning an estimate or None.

        Nonfinite/nonpositive pressure and invalid times reset and return None.
        At 5e-8 Torr or below the requested goal is reached and history is reset.
        ``reason`` explains the latest outcome, without changing the fit rules.
        """
        try:
            timestamp, pressure = float(timestamp_monotonic), float(pressure_torr)
        except (TypeError, ValueError):
            self.reset()
            self.reason = "invalid timestamp or pressure"
            return None
        if not math.isfinite(timestamp) or not math.isfinite(pressure) or pressure <= 0:
            self.reset()
            self.reason = "timestamp must be finite and pressure finite and positive"
            return None
        if pressure <= 5e-8:
            self.reset()
            self.reason = "goal reached: pressure is at or below 5e-8 Torr"
            return None
        if self._last_timestamp is not None:
            gap = timestamp - self._last_timestamp
            if gap <= 0:
                self.reset()
                self.reason = "timestamp did not increase; history cleared"
                return None
            if gap > self.max_gap_seconds:
                self._seed(timestamp, pressure)
                self.reason = f"sample gap {gap:.1f}s exceeds {self.max_gap_seconds:g}s; collecting fresh data"
                return None

        previous_pressure = self._last_pressure
        upward = previous_pressure is not None and pressure > previous_pressure
        self._rising_streak = self._rising_streak + 1 if upward else 0
        recent = [(when, value) for when, value in self._samples if timestamp - when <= 15]
        jump = upward and pressure > previous_pressure * 1.01
        cumulative_rise = recent and pressure > min(value for _, value in recent) * 1.01
        sustained_rise = False
        if len(recent) >= 4 and timestamp - recent[0][0] >= 2:
            slope, _ = _log_fit(recent + [(timestamp, pressure)])
            sustained_rise = slope > 0 and pressure > recent[0][1] * 1.002
        if jump or cumulative_rise or self._rising_streak >= 3 or sustained_rise:
            self._seed(timestamp, pressure)
            self.reason = "pressure rise detected; history cleared"
            return None

        self._samples.append((timestamp, pressure))
        self._last_timestamp = timestamp
        self._last_pressure = pressure
        while self._samples and timestamp - self._samples[0][0] > self.window_seconds:
            self._samples.popleft()
        if upward:
            self.reason = "pressure increased since the previous reading"
            return None
        # A formerly good global fit must not keep predicting while the most
        # recent readings have stopped falling. Quantization/noise below 0.1%
        # over at least ten seconds does not establish continuing pump-down.
        if recent and timestamp - recent[0][0] >= 10:
            if pressure > recent[0][1] * 0.999:
                self.reason = "recent pressure plateau: less than 0.1% decrease over at least 10s"
                return None
        if len(self._samples) < self.min_samples:
            self.reason = f"collecting samples: {len(self._samples)}/{self.min_samples}"
            return None
        span = timestamp - self._samples[0][0]
        if span < self.min_span_seconds:
            self.reason = f"collecting history: {span:.1f}/{self.min_span_seconds:g}s"
            return None
        if pressure > self._samples[0][1] * (1 - self.min_relative_drop):
            drop = 100 * (1 - pressure / self._samples[0][1])
            self.reason = f"net pressure drop {drop:.2f}% is below required {100 * self.min_relative_drop:g}%"
            return None

        slope, quality = _log_fit(self._samples)
        if slope >= 0:
            self.reason = "fitted pressure trend is not falling"
            return None
        if quality < self.min_r_squared:
            self.reason = f"fit R² {quality:.3f} is below required {self.min_r_squared:g}"
            return None
        target = 1e-7 if pressure > 1e-7 else 5e-8
        seconds = math.log(target / pressure) / slope
        if not math.isfinite(seconds) or seconds <= 0:
            self.reason = "predicted remaining time is not finite and positive"
            return None
        if seconds > self.max_eta_seconds:
            self.reason = f"predicted ETA {seconds / 3600:.1f}h exceeds limit {self.max_eta_seconds / 3600:g}h"
            return None
        self.reason = f"estimate available; falling trend, R²={quality:.3f}"
        return Estimate(seconds=seconds, target_torr=target, r_squared=quality)
