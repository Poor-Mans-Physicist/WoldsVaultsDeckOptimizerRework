"""Search-quality bench for the tagged SA kernel.

For each problem (deck, panel config, class, core set) and search config,
runs `replicates` independent best-of-`restarts` searches and reports the
relative gap to the best score ever recorded for that problem. Best-known
scores persist in scripts/bench_best_known.json and are raised by any run.

Run:  uv run python scripts/bench_search.py [--configs a,b] [--reference]
          [--replicates N] [--only SUBSTR] [--per-problem]

Every restart is one Rust job; run_sa_jobs spreads them over all cores.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import zlib
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.config import DECKS, _get_test_configs  # noqa: E402
from src.main import deck_implicits  # noqa: E402
from src.simulate import candidate_cores, run_sa_jobs, sa_optimize_tagged  # noqa: E402
from src.types import CardClass, CoreType  # noqa: E402

BENCH_CFG = Path(__file__).with_name("bench_search.yaml")
BEST_FILE = Path(__file__).with_name("bench_best_known.json")
EXACT_TOL = 1e-9


def problem_id(p: dict) -> str:
    return f"{p['deck']} | {p['panel']} | {p['cls']} | {'+'.join(sorted(p['cores']))}"


def resolve(p: dict):
    """Problem spec → (constrained deck, class, core set, implicits)."""
    deck = next((d for d in DECKS if d.name == p["deck"]), None)
    if deck is None:
        raise SystemExit(f"bench: unknown deck {p['deck']!r}")
    panel = next((c for c in _get_test_configs(deck) if c[0] == p["panel"]), None)
    if panel is None:
        raise SystemExit(f"bench: unknown panel {p['panel']!r} for {p['deck']}")
    cdeck = deck.with_constraints(panel[1], panel[2])
    cls = CardClass(p["cls"])
    cores = frozenset(CoreType(c) for c in p["cores"])
    if cores not in candidate_cores(cls, cdeck):
        print(f"bench: WARNING {problem_id(p)} is not a candidate core set "
              f"for this deck/class (benched anyway)", flush=True)
    return cdeck, cls, cores, deck_implicits(deck)


def run_config(resolved, cname, cfg, reps):
    """All replicates × restarts of one config as Rust jobs (run_sa_jobs,
    all cores). Returns ([(pid, rep, best score)], wall seconds)."""
    sched = (cfg["schedule"][0], [float(v) for v in cfg["schedule"][1]])
    p2 = cfg.get("phase2")
    p2 = (int(p2[0]), int(p2[1]), p2[2], [float(v) for v in p2[3]]) if p2 else None
    # Phase-2 configs need every restart of a replicate in one kernel job
    # (the top-K pick happens inside); plain configs run one job per restart.
    per_job = cfg["restarts"] if p2 else 1
    keys, jobs = [], []
    for pid, (cdeck, cls, cores, imps) in resolved.items():
        for rep in range(reps):
            for k in range(cfg["restarts"] // per_job):
                seed = zlib.crc32(f"{pid}|{cname}|{rep}|{k}".encode())
                keys.append((pid, rep))
                jobs.append(sa_optimize_tagged(
                    cdeck, cls, cores, n_iter=cfg["n_iter"], implicits=imps,
                    seed=seed, schedule=sched, search_opts=list(cfg.get("opts", [])),
                    restarts=per_job, phase2=p2, defer=True,
                ))
    t0 = time.perf_counter()
    outs = run_sa_jobs(jobs)
    wall = time.perf_counter() - t0
    best = {}
    for key, (_, score) in zip(keys, outs):
        best[key] = max(best.get(key, float("-inf")), score)
    return [(pid, rep, sc) for (pid, rep), sc in best.items()], wall


def load_best() -> dict:
    if BEST_FILE.exists():
        return json.loads(BEST_FILE.read_text(encoding="utf-8"))
    return {}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="", help="comma list (default: all in `configs`)")
    ap.add_argument("--reference", action="store_true", help="run the `reference` configs too")
    ap.add_argument("--replicates", type=int, default=0)
    ap.add_argument("--only", default="", help="substring filter on problem ids")
    ap.add_argument("--per-problem", action="store_true")
    args, _ = ap.parse_known_args()

    spec = yaml.safe_load(BENCH_CFG.read_text(encoding="utf-8"))
    problems = [p for p in spec["problems"] if args.only in problem_id(p)]
    configs = dict(spec["configs"])
    if args.configs:
        want = args.configs.split(",")
        pool_cfgs = {**configs, **spec.get("reference", {})}
        missing = [c for c in want if c not in pool_cfgs]
        if missing:
            raise SystemExit(f"bench: unknown config(s) {missing}")
        configs = {c: pool_cfgs[c] for c in want}
    refs = dict(spec.get("reference", {})) if args.reference else {}
    reps = args.replicates or int(spec["replicates"])

    resolved = {problem_id(p): resolve(p) for p in problems}
    runs = [(c, cfg, reps) for c, cfg in configs.items()] + [(c, cfg, 1) for c, cfg in refs.items()]
    print(f"bench: {len(problems)} problems × {list(configs)} × {reps} reps"
          f"{' + reference ' + str(list(refs)) if refs else ''}", flush=True)

    t_wall = time.perf_counter()
    results, walls = [], {}
    for cname, cfg, n_reps in runs:
        rows, walls[cname] = run_config(resolved, cname, cfg, n_reps)
        results += [(pid, cname, rep, sc) for pid, rep, sc in rows]
        print(f"  {cname}: {walls[cname]:.1f}s", flush=True)

    prior = load_best()
    best = dict(prior)
    for pid, _, _, score in results:
        if score > best.get(pid, float("-inf")) + EXACT_TOL * abs(score):
            best[pid] = score
    for pid in sorted(best):
        if pid in prior and best[pid] > prior[pid]:
            print(f"  new best-known  {pid}: {prior[pid]:.4f} → {best[pid]:.4f}")
    BEST_FILE.write_text(json.dumps(dict(sorted(best.items())), indent=1), encoding="utf-8")

    pids = [problem_id(p) for p in problems]
    print(f"\nwall {time.perf_counter() - t_wall:.0f}s; gaps relative to best-known (%)")
    print(f"{'config':14s} {'median':>8s} {'mean':>8s} {'p90':>8s} {'max':>8s} {'exact':>7s} {'<0.5%':>7s} {'wall s':>8s}")
    for cname in list(configs) + list(refs):
        rows = [r for r in results if r[1] == cname]
        gaps = [100.0 * (best[r[0]] - r[3]) / best[r[0]] for r in rows]
        exact = sum(g <= 100 * EXACT_TOL for g in gaps) / len(gaps)
        near = sum(g < 0.5 for g in gaps) / len(gaps)
        print(f"{cname:14s} {statistics.median(gaps):8.3f} {statistics.mean(gaps):8.3f} "
              f"{pct(gaps, 0.9):8.3f} {max(gaps):8.3f} {exact:7.0%} {near:7.0%} {walls[cname]:8.1f}")

    if args.per_problem:
        names = list(configs)
        print(f"\nper-problem median gap (%)\n{'problem':70s} " + " ".join(f"{c:>12s}" for c in names))
        for pid in pids:
            cells = []
            for cname in names:
                g = [100.0 * (best[pid] - r[3]) / best[pid] for r in results if r[0] == pid and r[1] == cname]
                cells.append(f"{statistics.median(g):12.3f}")
            print(f"{pid:70s} " + " ".join(cells))


if __name__ == "__main__":
    main()
