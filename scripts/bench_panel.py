"""Panel-level bench: the spreadsheet's actual output under each strategy.

Every (deck, panel config, class) cell is solved by src.main.solve() with the
strategy's search settings; the cell's best NDM is compared to the best ever
recorded for that cell (scripts/bench_panel_best.json). Use it for anything
that changes which core sets get budget (halving) or the per-core-set budget.

Run:  uv run python scripts/bench_panel.py [--strategies a,b] [--reps N]
          [--only SUBSTR]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.config import DECKS  # noqa: E402
from src.main import panel_tasks, solve  # noqa: E402

BENCH_CFG = Path(__file__).with_name("bench_search.yaml")
BEST_FILE = Path(__file__).with_name("bench_panel_best.json")


def search_from(spec: dict) -> dict:
    p2 = spec.get("phase2")
    return {
        "n_iter": int(spec["n_iter"]),
        "restarts": int(spec["restarts"]),
        "phase2": (int(p2[0]), int(p2[1]), p2[2], [float(v) for v in p2[3]]) if p2 else None,
        "halving": spec.get("halving"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--only", default="", help="substring filter on cell ids")
    args, _ = ap.parse_known_args()

    strategies = yaml.safe_load(BENCH_CFG.read_text(encoding="utf-8"))["panel"]
    if args.strategies:
        want = args.strategies.split(",")
        missing = [w for w in want if w not in strategies]
        if missing:
            raise SystemExit(f"bench_panel: unknown strategy(s) {missing}")
        strategies = {w: strategies[w] for w in want}

    cells = [c for c in panel_tasks(DECKS) if args.only in f"{c[0]} | {c[1]} | {c[2].cls.value}"]
    ids = [f"{name} | {label} | {task.cls.value}" for name, label, task in cells]
    tasks = [c[2] for c in cells]
    print(f"bench_panel: {len(cells)} cells × {list(strategies)} × {args.reps} reps", flush=True)

    runs = {}
    for sname, spec in strategies.items():
        for rep in range(args.reps):
            t0 = time.perf_counter()
            best = solve(tasks, search_from(spec), seed_salt=f"{sname}|{rep}", log=lambda *_: None)
            wall = time.perf_counter() - t0
            runs.setdefault(sname, []).append((best, wall))
            print(f"  {sname} rep {rep}: {wall:.1f}s", flush=True)

    prior = json.loads(BEST_FILE.read_text(encoding="utf-8")) if BEST_FILE.exists() else {}
    known = dict(prior)
    for sname, reps in runs.items():
        for best, _ in reps:
            for cid, b in zip(ids, best):
                if b["score"] > known.get(cid, {}).get("score", -1.0):
                    known[cid] = {"score": b["score"],
                                  "cores": "+".join(sorted(c.value for c in b["cores"]))}
    raised = sum(1 for cid in known if cid in prior and known[cid]["score"] > prior[cid]["score"])
    BEST_FILE.write_text(json.dumps(dict(sorted(known.items())), indent=1), encoding="utf-8")
    if raised:
        print(f"  raised best-known on {raised} cells")

    print(f"\ngap per cell relative to best-known (%)")
    print(f"{'strategy':16s} {'mean':>7s} {'p90':>7s} {'max':>7s} {'exact':>6s} {'<0.5%':>6s} "
          f"{'other core':>10s} {'wall s':>7s}")
    for sname, reps in runs.items():
        gaps, other = [], 0
        for best, _ in reps:
            for cid, b in zip(ids, best):
                ref = known[cid]["score"]
                g = 100.0 * (ref - b["score"]) / ref if ref > 0 else 0.0
                gaps.append(g)
                if g > 0.5 and "+".join(sorted(c.value for c in b["cores"])) != known[cid]["cores"]:
                    other += 1
        gaps.sort()
        wall = statistics.mean(w for _, w in reps)
        print(f"{sname:16s} {statistics.mean(gaps):7.3f} {gaps[int(0.9 * len(gaps))]:7.3f} "
              f"{gaps[-1]:7.3f} {sum(g <= 1e-7 for g in gaps) / len(gaps):6.0%} "
              f"{sum(g < 0.5 for g in gaps) / len(gaps):6.0%} {other:10d} {wall:7.1f}")
    print("\n'other core' = cells >0.5% off whose winning core set differs from best-known's.")


if __name__ == "__main__":
    main()
