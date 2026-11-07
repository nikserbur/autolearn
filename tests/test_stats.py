"""skills/research/scripts/labkit/stats.py: the numbers the selection gates rest on.

    python3 -m unittest discover -s tests -v
"""
import math
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "research" / "scripts"))
from labkit import stats  # noqa: E402


class Moments(unittest.TestCase):
    def test_population_moments(self):
        mean, sd, skew, kurt = stats.moments([1, 2, 3, 4])
        self.assertEqual(mean, 2.5)
        self.assertAlmostEqual(sd, math.sqrt(1.25))
        self.assertAlmostEqual(skew, 0.0)
        self.assertAlmostEqual(kurt, 1.64)
        self.assertEqual(stats.moments([2, 2, 2]), (2, 0.0, 0.0, 3.0))


class DeflatedSharpe(unittest.TestCase):
    def test_expected_best_of_noise_grows_with_trials(self):
        self.assertEqual(stats.expected_max_sharpe(1, 1.0), 0.0)
        self.assertAlmostEqual(stats.expected_max_sharpe(100, 1.0), 2.53, delta=0.02)
        self.assertLess(stats.expected_max_sharpe(10, 1.0), stats.expected_max_sharpe(1000, 1.0))
        self.assertAlmostEqual(stats.expected_max_sharpe(100, 4.0), 2 * stats.expected_max_sharpe(100, 1.0))

    def test_psr_is_a_half_at_the_benchmark_and_falls_with_trials(self):
        self.assertAlmostEqual(stats.psr(0.1, 0.1, 250, 0.0, 3.0), 0.5)
        sr, n = 0.15, 250
        dsr = [stats.psr(sr, stats.expected_max_sharpe(k, 1 / n), n, 0.0, 3.0) for k in (1, 10, 100, 1000)]
        self.assertEqual(dsr, sorted(dsr, reverse=True))
        self.assertGreater(dsr[0], 0.98)
        self.assertLess(dsr[-1], 0.5)

    def test_fat_tails_and_negative_skew_lower_confidence(self):
        normal = stats.psr(0.1, 0.0, 250, 0.0, 3.0)
        self.assertLess(stats.psr(0.1, 0.0, 250, -1.0, 10.0), normal)

    def test_minimum_track_length(self):
        self.assertAlmostEqual(stats.min_track_length(0.1, 100), (stats.expected_max_sharpe(100, 1.0) / 0.1) ** 2)
        self.assertEqual(stats.min_track_length(0.1, 1), 0.0)
        self.assertEqual(stats.min_track_length(-0.1, 10), math.inf)


class Bootstrap(unittest.TestCase):
    def series(self, mean, n=250, seed=1):
        rng = random.Random(seed)
        return [mean + rng.gauss(0, 1) for _ in range(n)]

    def test_p_value_separates_an_edge_from_noise(self):
        self.assertLess(stats.p_mean_above_zero(self.series(0.4)), 0.01)
        self.assertGreater(stats.p_mean_above_zero(self.series(0.0, seed=3)), 0.05)
        self.assertEqual(stats.p_mean_above_zero(self.series(0.1)), stats.p_mean_above_zero(self.series(0.1)))

    def test_interval_covers_the_mean_and_widens_with_dependence(self):
        xs = self.series(0.2)
        lo, hi = stats.interval(xs)
        self.assertLess(lo, sum(xs) / len(xs))
        self.assertGreater(hi, sum(xs) / len(xs))
        rng, level, walk = random.Random(5), 0.0, []
        for _ in range(250):  # strongly autocorrelated days
            level = 0.9 * level + rng.gauss(0, 1)
            walk.append(level)
        narrow = stats.interval(walk, block=1)
        wide = stats.interval(walk, block=20)
        self.assertGreater(wide[1] - wide[0], narrow[1] - narrow[0])

    def test_resample_indices_wrap_and_keep_length(self):
        idx = stats.stationary_indices(10, 3, random.Random(0))
        self.assertEqual(len(idx), 10)
        self.assertTrue(all(0 <= i < 10 for i in idx))


class FalseDiscoveries(unittest.TestCase):
    def test_benjamini_yekutieli(self):
        p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        self.assertEqual(stats.benjamini_yekutieli(p, 0.05), [True] + [False] * 9)
        self.assertEqual(stats.benjamini_yekutieli([0.0001, 0.0002], 0.05), [True, True])
        self.assertEqual(stats.benjamini_yekutieli([]), [])

    def test_lord_spends_and_earns_wealth_in_order(self):
        self.assertAlmostEqual(sum(stats.gamma(t) for t in range(1, 200000)), 1.0, places=4)
        levels = stats.lord_levels([0.001, 0.2, 0.2], 0.05)
        self.assertAlmostEqual(levels[0][0], stats.gamma(1) * 0.025)
        self.assertTrue(levels[0][1])
        quiet = stats.lord_levels([0.9, 0.2, 0.2], 0.05)
        self.assertGreater(levels[1][0], quiet[1][0])  # a discovery earns wealth for the next tests
        self.assertEqual(stats.lord_levels([0.001, 0.2], 0.05), levels[:2])  # later tests change nothing earlier

    def test_many_null_tests_rarely_discover(self):
        rng = random.Random(11)
        found = sum(f for _, f in stats.lord_levels([rng.random() for _ in range(200)], 0.05))
        self.assertLessEqual(found, 2)


if __name__ == "__main__":
    unittest.main()
