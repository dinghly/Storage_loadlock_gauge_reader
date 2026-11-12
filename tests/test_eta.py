"""Known pump-down curves, threshold changes, and suppression on venting."""

import math
import unittest

from storage_loadlock_gauge_reader.eta import PumpdownEstimator, format_eta


class PumpdownEstimatorTests(unittest.TestCase):
    def falling(self, estimator, start=0, duration=120, initial=1e-5, rate=.01):
        result = None
        for second in range(duration + 1):
            result = estimator.update(start + second, initial * math.exp(-rate * second))
        return result

    def test_known_exponential_gives_correct_remaining_time(self):
        estimator = PumpdownEstimator()
        result = self.falling(estimator)
        self.assertIsNotNone(result)
        self.assertEqual(result.target_torr, 1e-7)
        self.assertAlmostEqual(result.seconds, math.log(100) / .01 - 120)
        self.assertAlmostEqual(result.r_squared, 1)

    def test_minimum_history_and_decline_are_required(self):
        estimator = PumpdownEstimator()
        for second in range(60):
            self.assertIsNone(estimator.update(second, 1e-5 * math.exp(-.01 * second)))
        self.assertIsNotNone(estimator.update(60, 1e-5 * math.exp(-.6)))
        for second in range(400):
            self.assertIsNone(PumpdownEstimator().update(second, 1e-5))
        estimator = PumpdownEstimator()
        for second in range(301):
            self.assertIsNone(estimator.update(second, 1e-5 * math.exp(-.00001 * second)))

    def test_sixty_minute_half_life_is_estimable(self):
        estimator = PumpdownEstimator()
        rate = math.log(2) / 3600
        result = self.falling(estimator, duration=300, initial=1e-7, rate=rate)
        self.assertIsNotNone(result)
        self.assertEqual(result.target_torr, 5e-8)
        self.assertAlmostEqual(result.seconds, 3300)

    def test_switches_target_then_removes_eta_at_final_goal(self):
        estimator = PumpdownEstimator()
        first = self.falling(estimator, duration=100, initial=3e-7)
        self.assertEqual(first.target_torr, 1e-7)
        second = estimator.update(110, 3e-7 * math.exp(-1.1))
        self.assertEqual(second.target_torr, 5e-8)
        self.assertIsNone(estimator.update(120, 5e-8))
        self.assertIsNone(estimator.update(121, 4.9e-8))
        self.assertIsNone(estimator.update(122, 8e-8))

    def test_any_upward_reading_hides_eta_and_large_jump_discards_history(self):
        estimator = PumpdownEstimator()
        self.assertIsNotNone(self.falling(estimator))
        last = 1e-5 * math.exp(-1.2)
        self.assertIsNone(estimator.update(121, last * 1.001))
        self.assertIsNone(estimator.update(122, last * 1.1))
        for second in range(123, 182):
            self.assertIsNone(estimator.update(second, last * 1.1 * math.exp(-.01 * (second - 122))))
        self.assertIsNotNone(estimator.update(182, last * 1.1 * math.exp(-.6)))

    def test_three_tiny_increases_reset_old_pumpdown(self):
        estimator = PumpdownEstimator()
        self.falling(estimator)
        last = 1e-5 * math.exp(-1.2)
        for offset in (1, 2, 3):
            self.assertIsNone(estimator.update(120 + offset, last * (1.00001 ** offset)))
        self.assertIsNone(estimator.update(124, last * .99))

    def test_recent_cumulative_rise_cannot_use_old_decreasing_fit(self):
        estimator = PumpdownEstimator()
        self.falling(estimator)
        last = 1e-5 * math.exp(-1.2)
        for offset, factor in enumerate((1.005, 1.004, 1.009, 1.008, 1.013, 1.012), 1):
            result = estimator.update(120 + offset, last * factor)
            # Initial tiny rebound has an isolated decreasing sample; once the
            # recent rise is established, even the downward ticks must be hidden.
            if offset >= 5:
                self.assertIsNone(result)

    def test_plateau_and_poorly_fitted_curve_do_not_predict(self):
        estimator = PumpdownEstimator()
        for second in range(400):
            self.assertIsNone(estimator.update(second, 1e-5))
        estimator = PumpdownEstimator()
        result = None
        for second in range(301):
            pressure = 1e-5 if second < 295 else 8e-6
            result = estimator.update(second, pressure)
        self.assertIsNone(result)

    def test_recent_plateau_hides_a_previously_good_pumpdown_fit(self):
        estimator = PumpdownEstimator()
        self.assertIsNotNone(self.falling(estimator))
        pressure = 1e-5 * math.exp(-1.2)
        for second in range(121, 141):
            result = estimator.update(second, pressure)
        self.assertIsNone(result)

    def test_nonfinite_invalid_or_nonincreasing_time_resets_history(self):
        for timestamp, pressure in ((math.nan, 1e-5), (121, math.inf), (121, 0),
                                    (121, -1), (121, None), (120, 1e-5), (119, 1e-5)):
            estimator = PumpdownEstimator()
            self.falling(estimator)
            self.assertIsNone(estimator.update(timestamp, pressure))
            self.assertIsNone(estimator.update(122, 1e-5))

    def test_explicit_reset_and_long_gap_require_fresh_history(self):
        estimator = PumpdownEstimator()
        self.falling(estimator)
        estimator.reset()
        self.assertIsNone(estimator.update(121, 1e-6))
        estimator = PumpdownEstimator()
        self.falling(estimator)
        self.assertIsNone(estimator.update(1000, 1e-6))
        self.assertIsNone(estimator.update(1001, .9e-6))

    def test_old_values_leave_rolling_window_and_excessive_eta_is_hidden(self):
        estimator = PumpdownEstimator(window_seconds=60)
        self.assertIsNotNone(self.falling(estimator, duration=600, initial=1e-3))
        self.assertEqual(len(estimator._samples), 61)
        estimator = PumpdownEstimator(max_eta_seconds=100)
        self.assertIsNone(self.falling(estimator))

    def test_invalid_configuration_is_rejected(self):
        for kwargs in ({"window_seconds": 30}, {"min_span_seconds": 0}, {"min_samples": 2},
                       {"min_r_squared": 1.1}, {"max_eta_seconds": math.inf},
                       {"min_relative_drop": -1}, {"max_gap_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PumpdownEstimator(**kwargs)


class EtaFormattingTests(unittest.TestCase):
    def test_minutes_and_hours_are_short_and_rounded_up(self):
        for seconds, text in ((0, '1M'), (1, '1M'), (60, '1M'), (61, '2M'),
                              (300, '5M'), (3600, '60M'), (5940, '99M'),
                              (5941, '2H'), (86400, '24H')):
            with self.subTest(seconds=seconds):
                self.assertEqual(format_eta(seconds), text)
                self.assertLessEqual(len(format_eta(seconds)), 3)

    def test_nonfinite_negative_and_too_large_values_rejected(self):
        for seconds in (-1, math.inf, math.nan, 100 * 3600):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                format_eta(seconds)


if __name__ == '__main__':
    unittest.main()
