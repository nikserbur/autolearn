"""Statistics that know how many things were tried. Standard library only, Python 3.9+.

- Sharpe ratio per period, its skewness and kurtosis, the probabilistic Sharpe ratio (PSR) and the
  deflated Sharpe ratio (DSR): the probability that the true Sharpe exceeds the best one N trials of
  pure noise would show (Bailey and Lopez de Prado, 2014).
- Minimum track length: how many periods it takes for this Sharpe to beat that best-of-N noise.
- A stationary block bootstrap (Politis and Romano, 1994) over periods, so days that move together
  stay together: a one-sided p-value for "the mean is above zero" and an interval for a paired
  difference.
- Benjamini-Yekutieli within a round (false discovery rate under any dependence) and LORD++ across
  rounds (online false discovery rate: each test's level depends only on the tests before it;
  Javanmard and Montanari 2018, Ramdas et al. 2017).
"""
from __future__ import annotations

import math
import random
from statistics import NormalDist, fmean

EULER = 0.5772156649015329
NORMAL = NormalDist()


def moments(xs: list):
    """(mean, standard deviation, skewness, kurtosis) with the population formulas; kurtosis is 3 for a normal."""
    n, mean = len(xs), fmean(xs)
    m2 = sum((x - mean) ** 2 for x in xs) / n
    if m2 == 0:
        return mean, 0.0, 0.0, 3.0
    m3 = sum((x - mean) ** 3 for x in xs) / n
    m4 = sum((x - mean) ** 4 for x in xs) / n
    return mean, math.sqrt(m2), m3 / m2 ** 1.5, m4 / m2 ** 2


def sharpe(xs: list) -> float:
    mean, sd, _, _ = moments(xs)
    return mean / sd if sd else 0.0


def expected_max_sharpe(n_trials: int, variance: float) -> float:
    """The Sharpe ratio the best of n_trials independent noise strategies is expected to show."""
    if n_trials <= 1:
        return 0.0
    z = (1 - EULER) * NORMAL.inv_cdf(1 - 1 / n_trials) + EULER * NORMAL.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(variance) * z


def psr(sr: float, benchmark: float, n: int, skew: float, kurt: float) -> float:
    """Probability that the true per-period Sharpe exceeds `benchmark`, given n periods."""
    spread = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if n < 2 or spread <= 0:
        return 0.0
    return NORMAL.cdf((sr - benchmark) * math.sqrt(n - 1) / math.sqrt(spread))


def min_track_length(sr: float, n_trials: int) -> float:
    """Periods needed before a per-period Sharpe of `sr` beats the expected best of n_trials noise
    strategies (the standard error of a Sharpe estimate is about 1/sqrt(periods))."""
    if n_trials <= 1:
        return 0.0
    if sr <= 0:
        return math.inf
    return (expected_max_sharpe(n_trials, 1.0) / sr) ** 2


def stationary_indices(n: int, block: float, rng: random.Random) -> list:
    """One stationary-bootstrap resample of range(n): blocks of geometric length with mean `block`, wrapping."""
    out, i = [], rng.randrange(n)
    for _ in range(n):
        out.append(i)
        i = rng.randrange(n) if rng.random() < 1 / block else (i + 1) % n
    return out


def bootstrap_means(xs: list, block: float, draws: int, seed: int) -> list:
    rng = random.Random(seed)
    return [fmean(xs[i] for i in stationary_indices(len(xs), block, rng)) for _ in range(draws)]


def p_mean_above_zero(xs: list, block: float = 5, draws: int = 2000, seed: int = 0) -> float:
    """One-sided bootstrap p-value of mean > 0: the series is centred, so the resamples show what
    noise alone gives, and the p-value is how often noise reaches the observed mean."""
    mean = fmean(xs)
    centred = [x - mean for x in xs]
    hits = sum(m >= mean for m in bootstrap_means(centred, block, draws, seed))
    return (1 + hits) / (1 + draws)


def interval(xs: list, block: float = 5, draws: int = 2000, seed: int = 0, level: float = 0.95):
    """Percentile interval of the mean under the stationary block bootstrap."""
    means = sorted(bootstrap_means(xs, block, draws, seed))
    tail = (1 - level) / 2
    return means[int(tail * (draws - 1))], means[int(math.ceil((1 - tail) * (draws - 1)))]


def benjamini_yekutieli(pvalues: list, q: float = 0.05) -> list:
    """Which hypotheses are discoveries at false discovery rate q, under any dependence."""
    m = len(pvalues)
    if not m:
        return []
    harmonic = sum(1 / i for i in range(1, m + 1))
    order = sorted(range(m), key=lambda i: pvalues[i])
    cutoff = 0
    for rank, i in enumerate(order, 1):
        if pvalues[i] <= rank * q / (m * harmonic):
            cutoff = rank
    keep = set(order[:cutoff])
    return [i in keep for i in range(m)]


def gamma(t: int) -> float:
    """The LORD++ spending sequence: non-increasing, summing to one over t = 1, 2, ..."""
    return 6 / (math.pi ** 2 * t ** 2)


def lord_levels(pvalues: list, alpha: float = 0.05, wealth: float = None):
    """LORD++ in the order the tests were made: (level, discovery) for each p-value. A test's level
    depends only on earlier tests, so no later result can change an earlier decision."""
    w0 = alpha / 2 if wealth is None else wealth
    discoveries, out = [], []
    for t, p in enumerate(pvalues, 1):
        level = gamma(t) * w0
        if discoveries:
            level += (alpha - w0) * gamma(t - discoveries[0])
            level += alpha * sum(gamma(t - tau) for tau in discoveries[1:])
        found = p <= level
        if found:
            discoveries.append(t)
        out.append((level, found))
    return out
