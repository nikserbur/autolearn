"""A strategy whose daily result is the given mean plus unit noise, for the selection tests.

    python3 series_sim.py OUT_DIR MEAN SEED [DAYS]

Writes OUT_DIR/daily.tsv (period, pnl) and OUT_DIR/metrics.tsv (net_per_trade, trades).
"""
import pathlib
import random
import sys

out, mean, seed = pathlib.Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
days = int(sys.argv[4]) if len(sys.argv) > 4 else 250
rng = random.Random(seed)
values = [mean + rng.gauss(0, 1) for _ in range(days)]
out.mkdir(parents=True, exist_ok=True)
(out / "daily.tsv").write_text("period\tpnl\n" + "".join(f"d{i:03d}\t{v:.6f}\n" for i, v in enumerate(values)))
(out / "metrics.tsv").write_text(f"net_per_trade\ttrades\n{sum(values) / days:.6f}\t{days}\n")
