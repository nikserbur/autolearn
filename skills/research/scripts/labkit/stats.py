"""Statistics that know how many things were tried. Standard library only, Python 3.9+.

- Sharpe ratio per period, its skewness and kurtosis, the probabilistic Sharpe ratio (PSR) and the
  deflated Sharpe ratio (DSR): the probability that the true Sharpe exceeds the best one N trials of
  pure noise would show (Bailey and Lopez de Prado, 2014).
- Minimum track length: how many periods it takes for this Sharpe to beat that best-of-N noise.
- A stationary block bootstrap (Politis and Romano, 1994) over periods, with the block length taken
  from the data (Politis and White, 2004), so days that move together stay together: the standard
  error and interval of a mean or of a paired difference, and a one-sided p-value for mean > 0.
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


def min_track_length_95(sr: float, n_trials: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Periods needed for a deflated Sharpe of 0.95: (z_N + 1.645 * spread) / SR, squared, where z_N
    is the expected best Sharpe of n_trials noise strategies in standard errors."""
    if sr <= 0:
        return math.inf
    spread = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    z = expected_max_sharpe(n_trials, 1.0)
    return ((z + NORMAL.inv_cdf(0.95) * math.sqrt(max(spread, 0.0))) / sr) ** 2


def auto_block(xs: list) -> float:
    """Expected block length for the stationary bootstrap from the data's own autocorrelation
    (Politis and White 2004, with the correction of Patton, Politis and White 2009), doubled, within
    [1, n/4]. Doubling, with the Student tail in bootstrap(), keeps the false-positive rate near its
    level for moderate dependence; on 250 days at 5%: 3.3% for independent days, 5.2% at lag-one
    correlation 0.5, 9% at 0.9 (the plain estimate gave 4%, 7.5% and 13.5%)."""
    n, mean = len(xs), fmean(xs)
    centred = [x - mean for x in xs]
    var = sum(c * c for c in centred) / n
    if n < 8 or var == 0:
        return 1.0
    kn = max(5, math.ceil(math.sqrt(math.log10(n))))
    m_max = math.ceil(math.sqrt(n)) + kn
    m_max = min(m_max, n - 1)
    cov = [sum(centred[i] * centred[i + k] for i in range(n - k)) / n for k in range(m_max + 1)]
    rho = [c / var for c in cov]
    bound = 2 * math.sqrt(math.log10(n) / n)
    m_hat = next((m for m in range(1, m_max - kn + 1) if all(abs(rho[m + j]) < bound for j in range(1, kn + 1))), m_max)
    big_m = min(2 * m_hat, m_max)
    flat = lambda s: 1.0 if abs(s) <= 0.5 else (2 * (1 - abs(s)) if abs(s) <= 1 else 0.0)
    g = sum(flat(k / big_m) * k * cov[k] for k in range(1, big_m + 1)) * 2
    spectrum0 = cov[0] + 2 * sum(flat(k / big_m) * cov[k] for k in range(1, big_m + 1))
    if spectrum0 <= 0:
        return 1.0
    block = 2 * (2 * g * g / (2 * spectrum0 * spectrum0)) ** (1 / 3) * n ** (1 / 3)
    return min(max(block, 1.0), n / 4)


def t_upper(t: float, df: float) -> float:
    """P(T > t) for Student's t with df degrees of freedom, by the regularised incomplete beta."""
    x = df / (df + t * t)
    a, b = df / 2, 0.5

    def fraction(a, b, x):
        tiny = 1e-300
        c, d = 1.0, 1 - (a + b) * x / (a + 1)
        d = 1 / (d if abs(d) > tiny else tiny)
        h = d
        for m in range(1, 300):
            for num in (m * (b - m) * x / ((a - 1 + 2 * m) * (a + 2 * m)),
                        -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 1 + 2 * m))):
                d = 1 + num * d
                d = 1 / (d if abs(d) > tiny else tiny)
                c = 1 + num / c
                h *= d * c
            if abs(d * c - 1) < 1e-13:
                break
        return h

    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)) \
        if 0 < x < 1 else 0.0
    if x <= 0:
        beta = 0.0
    elif x >= 1:
        beta = 1.0
    elif x < (a + 1) / (a + b + 2):
        beta = front * fraction(a, b, x) / a
    else:
        beta = 1 - front * fraction(b, a, 1 - x) / b
    return beta / 2 if t > 0 else 1 - beta / 2


def bootstrap(xs: list, block: float, draws: int = 2000, seed: int = 0, level: float = 0.95) -> dict:
    """One pass of the stationary block bootstrap (Politis and Romano, 1994): blocks of geometric
    length with mean `block`, wrapping around, summed with prefix sums. The resampled means give the
    standard error under the series' own dependence and a percentile interval; the one-sided p-value of
    mean > 0 is the Student tail of mean / standard error with n / block - 1 degrees of freedom, which
    has no floor at 1/draws. Long series use fewer draws (at least 400), since only the spread and the
    interval are taken from them."""
    n, mean = len(xs), fmean(xs)
    draws = max(400, min(draws, int(6e6 * block / n)))
    prefix = [0.0]
    for x in xs + xs:
        prefix.append(prefix[-1] + x)
    rng, restart = random.Random(seed), 1 / block
    log_stay = math.log(1 - restart) if restart < 1 else None
    means = []
    for _ in range(draws):
        total, left = 0.0, n
        while left:
            start = rng.randrange(n)
            length = 1 if log_stay is None else 1 + int(math.log(1 - rng.random()) / log_stay)
            length = min(length, left)
            total += prefix[start + length] - prefix[start]
            left -= length
        means.append(total / n)
    se = statistics_stdev(means)
    p = t_upper(mean / se, max(n / block - 1, 2.0)) if se > 0 else (0.0 if mean > 0 else 1.0)
    means.sort()
    tail = (1 - level) / 2
    return {"p": p, "lo": means[int(tail * (draws - 1))], "hi": means[int(math.ceil((1 - tail) * (draws - 1)))],
            "se": se, "draws": draws}


def statistics_stdev(xs: list) -> float:
    mean = fmean(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0


def p_mean_above_zero(xs: list, block: float = 5, draws: int = 2000, seed: int = 0) -> float:
    return bootstrap(xs, block, draws, seed)["p"]


def interval(xs: list, block: float = 5, draws: int = 2000, seed: int = 0, level: float = 0.95):
    result = bootstrap(xs, block, draws, seed, level)
    return result["lo"], result["hi"]


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
