"""A toy one-day strategy with a planted look-ahead, for the research-protocol tests.

    python3 toy_sim.py OUT_DIR [--bars N] [--honest]

Every bar is a decision (long or flat) and goes into the trace. Momentum reads the next bar's
close unless --honest; --bars cuts the data, as a truncation test does, and the look-ahead then
quietly falls back to the last close it has. Writes OUT_DIR/metrics.tsv and OUT_DIR/trace.tsv.
"""
import datetime
import pathlib
import random
import sys

out = pathlib.Path(sys.argv[1])
bars = int(sys.argv[sys.argv.index("--bars") + 1]) if "--bars" in sys.argv else 390
honest = "--honest" in sys.argv
rng = random.Random(7)
close, imbalance = [], []
for _ in range(390):
    close.append(5000.0 + rng.gauss(0, 1.0) + (close[-1] - 5000.0 if close else 0.0))
    imbalance.append(round(rng.uniform(-1, 1), 3))
close, imbalance = close[:bars], imbalance[:bars]

start = datetime.datetime(2026, 3, 2, 9, 30)
rows, net, trades, busy_until = [], 0.0, 0, -1
for t in range(5, bars):
    ts = (start + datetime.timedelta(minutes=t)).isoformat()
    later = close[t + 1] if t + 1 < bars else close[t]
    momentum = close[t] / close[t - 5] - 1 if honest else later / close[t - 4] - 1
    inputs = f"imbalance={imbalance[t]};momentum={momentum:.6f}"
    if t <= busy_until or not (imbalance[t] > 0.3 and momentum > 0):
        rows.append(f"{ts}\tsignal\tflat\t{inputs}\t{ts}\tno entry\t")
        continue
    exit_t = min(t + 3, bars - 1)
    gain = close[exit_t] - 0.25 - (close[t] + 0.25)
    rows.append(f"{ts}\tsignal\tlong\t{inputs}\t{ts}\tboth up\t{gain:.2f}")
    net, trades, busy_until = net + gain, trades + 1, exit_t

out.mkdir(parents=True, exist_ok=True)
(out / "trace.tsv").write_text("ts\tstage\taction\tinputs\tinputs_ts\treason\toutcome\n" + "\n".join(rows) + "\n")
(out / "metrics.tsv").write_text(f"net\ttrades\tnet_per_trade\n{net:.2f}\t{trades}\t{net / max(trades, 1):.3f}\n")
