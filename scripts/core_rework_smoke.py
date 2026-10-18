"""Core rework smoke test: best deck + core set per archetype under the reworked cores (src/rework.py).

Core sets are grown by beam search: every single core is screened, then the best `--beam` sets of each size are
extended by one core and screened again, up to the deck's core slots. The best `--final` sets per (archetype, deck)
get the full two-phase budget. `--exhaustive` screens every legal set instead (the beam's ground truth).

Run:  uv run --no-sync python scripts/core_rework_smoke.py <deck_weights.json> max <out.json> [--k 3]
"""
import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import archetype as A  # noqa: E402
from src import rework as R  # noqa: E402
from src.config import DECKS  # noqa: E402
from src.implicits import DECK_IMPLICITS  # noqa: E402
from src.simulate import run_sa_jobs  # noqa: E402

PHASE2 = (4, 120000, "target", [0.1, 0.001, 0.005, 0.01])


def candidates(sd, only):
    """(label, deck, implicit keys, mystery) per candidate deck."""
    entry = sd["layout_entry"]
    shape, _ = A.deck_from_entry(entry)
    out = [(f"{entry['deck']} (stage shape: {'+'.join(entry['implicits'])})", shape, list(entry["implicits"]),
            "Mystery" in entry["deck"])]
    for d in DECKS:
        if only and d.key not in only:
            continue
        keys = [d.key] if d.key in DECK_IMPLICITS else []
        out.append((d.name, d, keys, False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights")
    ap.add_argument("stage")
    ap.add_argument("out")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--archetypes", default="all", help="comma list of archetype ids, or 'all' (generalist only)")
    ap.add_argument("--decks", default="")
    ap.add_argument("--beam", type=int, default=6)
    ap.add_argument("--final", type=int, default=3)
    ap.add_argument("--screen", default="2,20000")
    ap.add_argument("--full", default="4,40000")
    ap.add_argument("--exhaustive", action="store_true")
    args, _ = ap.parse_known_args()

    sd = A.load_stage(args.weights, args.stage)
    arch_all = {str(k): v for k, v in A.archetypes(sd, args.k).items()}
    arch_all["all"] = [b["family"] for b in sd["builds"]]
    arch = {a: arch_all[a] for a in args.archetypes.split(",")}
    models = {a: A.value_terms(sd, m) for a, m in arch.items()}
    cands = candidates(sd, set(args.decks.split(",")) if args.decks else None)
    r1, n1 = map(int, args.screen.split(","))
    r2, n2 = map(int, args.full.split(","))
    pairs = [(a, ci) for a in arch for ci in range(len(cands))]
    scores = {}
    t_all = time.perf_counter()
    n_jobs = 0

    def screen(keys):
        nonlocal n_jobs
        keys = [k for k in keys if k not in scores]
        if not keys:
            return
        jobs = []
        for (a, ci, cores) in keys:
            _label, d, imp_keys, mystery = cands[ci]
            jobs.append(R.job(d, imp_keys, mystery, cores, sd, models[a], n1, r1, None,
                              A.seed_for("rw-s", a, d.key, cores)))
        t0 = time.perf_counter()
        outs = run_sa_jobs(jobs)
        n_jobs += len(jobs)
        print(f"  screened {len(jobs)} sets in {time.perf_counter() - t0:.1f}s", flush=True)
        for k, (_raw, sc) in zip(keys, outs):
            scores[k] = sc

    max_slots = max(cands[ci][1].core_slots for _a, ci in pairs)
    if args.exhaustive:
        screen([(a, ci, s) for a, ci in pairs for s in R.all_sets(cands[ci][1])])
    else:
        frontier = {p: [()] for p in pairs}
        for size in range(1, max_slots + 1):
            keys = []
            for (a, ci) in pairs:
                d = cands[ci][1]
                if size > d.core_slots:
                    continue
                keys += [(a, ci, s) for s in R.extend(frontier[(a, ci)], d, size)]
            print(f"size {size}: {len(keys)} sets", flush=True)
            screen(keys)
            for (a, ci) in pairs:
                mine = sorted(((scores[k], k[2]) for k in scores if k[0] == a and k[1] == ci and len(k[2]) == size),
                              reverse=True)
                frontier[(a, ci)] = [s for _sc, s in mine[:args.beam]]
    t_screen = time.perf_counter() - t_all

    full_keys = []
    for (a, ci) in pairs:
        mine = sorted(((scores[k], k) for k in scores if k[0] == a and k[1] == ci), reverse=True)
        full_keys += [k for _sc, k in mine[:args.final]]
    jobs = [R.job(cands[ci][1], cands[ci][2], cands[ci][3], cores, sd, models[a], n2, r2, PHASE2,
                  A.seed_for("rw-f", a, cands[ci][1].key, cores)) for (a, ci, cores) in full_keys]
    t0 = time.perf_counter()
    outs = run_sa_jobs(jobs)
    t_full = time.perf_counter() - t0
    full = {k: (raw, sc) for k, (raw, sc) in zip(full_keys, outs)}

    result = {"stage": args.stage, "k": args.k, "beam": None if args.exhaustive else args.beam,
              "screen_jobs": n_jobs, "screen_s": t_screen, "full_jobs": len(full_keys), "full_s": t_full,
              "decks": [c[0] for c in cands], "archetypes": {}}
    for a in arch:
        rows = []
        for ci, (label, d, keys, mystery) in enumerate(cands):
            mine = {k[2]: v for k, v in scores.items() if k[0] == a and k[1] == ci}
            fin = {k[2]: v for k, v in full.items() if k[0] == a and k[1] == ci}
            if not fin:
                continue
            best = max(fin, key=lambda s: fin[s][1])
            rows.append({"deck": label, "core_slots": d.core_slots, "best_cores": list(best),
                         "score": fin[best][1], "grid_raw": [c[0] for c in fin[best][0]],
                         "screened": {"+".join(s): v for s, v in mine.items()}})
        rows.sort(key=lambda r: -r["score"])
        result["archetypes"][a] = {"members": arch[a], "decks": rows}
        print(f"\narchetype {a} ({len(arch[a])} builds): top decks")
        for r in rows[:8]:
            print(f"  {r['score']:8.3f}  {r['deck']:44s} {'+'.join(r['best_cores'])}")
    print(f"\nscreen {n_jobs} jobs in {t_screen:.0f}s; full {len(full_keys)} jobs in {t_full:.0f}s")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
