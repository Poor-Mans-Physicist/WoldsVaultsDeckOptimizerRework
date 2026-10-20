"""Optimize one deck per build archetype with the build-weighted objective (src/archetype.py).

Candidates: every DeckFAST deck whose implicit the ledger models, plus the stage's own layout shape, each with
every EVO core set (base + tag cores) up to its core slots. All candidates are screened on a small budget; per
archetype the best `--keep` (and any within `--margin` of the leader) get the full two-phase budget. Archetypes come
from the weights file's k-way clustering; "all" is a generalist over every build.

Run:  uv run python scripts/archetype_decks.py <deck_weights.json> <stage> <out.json> [--k 3] [--decks a,b]
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
from src.config import DECKS  # noqa: E402
from src.implicits import implicits_for_deck  # noqa: E402
from src.simulate import run_sa_jobs  # noqa: E402

SKIP_KINDS = {"adjacency", "chain", "empty_slots", "color_mismatch", "unique_groups"}
PHASE2 = (4, 120000, "target", [0.1, 0.001, 0.005, 0.01])


def candidate_decks(sd, only):
    """(deck, implicits, ledger implicit names, ledger implicit rules) per candidate deck."""
    out = []
    shape, imps = A.deck_from_entry(sd["layout_entry"])
    out.append((shape, imps, list(sd["layout_entry"]["implicits"]), dict(sd.get("implicit_rules") or {})))
    for d in DECKS:
        if only and d.key not in only:
            continue
        imps = implicits_for_deck(d.key)
        if any(t[0] in SKIP_KINDS for t in imps):
            continue
        names, rules = [], {}
        for (kind, value, groups, _colors, _extra) in imps:
            if kind in ("global", "freq"):
                names.append(d.key)
                if kind == "global":
                    rules[d.key] = {"kind": "global", "value": value, "groups": list(groups)}
            elif kind == "row_pos":
                names.append("cake")
            elif kind == "mirror":
                names.append("runic")
        out.append((d, imps, names, rules))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights")
    ap.add_argument("stage")
    ap.add_argument("out")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--decks", default="", help="comma deck keys (default: all modeled)")
    ap.add_argument("--screen", default="2,20000", help="restarts,iters for the screen")
    ap.add_argument("--full", default="8,40000", help="restarts,iters for survivors (plus phase 2)")
    ap.add_argument("--keep", type=int, default=12)
    ap.add_argument("--margin", type=float, default=0.02)
    ap.add_argument("--top", type=int, default=4, help="layouts written per archetype")
    ap.add_argument("--no-deluxe", action="store_true", help="no Deluxe cards or Deluxe core (an availability cap)")
    args, _ = ap.parse_known_args()

    sd = A.load_stage(args.weights, args.stage)
    arch = {str(k): v for k, v in A.archetypes(sd, args.k).items()}
    arch["all"] = [b["family"] for b in sd["builds"]]
    models = {a: A.value_terms(sd, m) for a, m in arch.items()}
    cands = candidate_decks(sd, set(args.decks.split(",")) if args.decks else None)
    dlx = not args.no_deluxe
    combos = [(ci, cores) for ci, (d, *_r) in enumerate(cands) for cores in A.core_sets(d, dlx)]
    print(f"{args.stage}: {len(arch)} archetypes x {len(cands)} decks x core sets = {len(arch) * len(combos)} "
          f"candidates", flush=True)

    r1, n1 = map(int, args.screen.split(","))
    t0 = time.perf_counter()
    jobs, keys = [], []
    for a in arch:
        for ci, cores in combos:
            d, imps, *_r = cands[ci]
            jobs.append(A.job(d, imps, cores, sd, models[a], n1, r1, None, A.seed_for("s", a, d.key, cores), dlx))
            keys.append((a, ci, cores))
    outs = run_sa_jobs(jobs)
    print(f"  screen: {len(jobs)} jobs in {time.perf_counter() - t0:.0f}s", flush=True)
    screen = {}
    for key, (_raw, score) in zip(keys, outs):
        screen[key] = score

    r2, n2 = map(int, args.full.split(","))
    t0 = time.perf_counter()
    jobs, keys = [], []
    for a in arch:
        ranked = sorted(((k, v) for k, v in screen.items() if k[0] == a), key=lambda kv: -kv[1])
        lead = ranked[0][1]
        keep = [k for k, v in ranked if v >= lead - args.margin * abs(lead)]
        keep = [k for k, _ in ranked[:max(args.keep, len(keep))]]
        for (_a, ci, cores) in keep:
            d, imps, *_r = cands[ci]
            jobs.append(A.job(d, imps, cores, sd, models[a], n2, r2, PHASE2, A.seed_for("f", a, d.key, cores), dlx))
            keys.append((a, ci, cores))
    outs = run_sa_jobs(jobs)
    print(f"  full: {len(jobs)} jobs in {time.perf_counter() - t0:.0f}s", flush=True)

    result = {"stage": args.stage, "k": args.k, "archetypes": {}, "layouts": {}}
    for a, members in arch.items():
        rows = sorted(((score, ci, cores, raw) for (aa, ci, cores), (raw, score) in zip(keys, outs) if aa == a),
                      key=lambda x: -x[0])
        result["archetypes"][a] = {"members": members, "best": []}
        print(f"\n  archetype {a} ({len(members)} builds): " + ", ".join(sorted(m.split(':')[1] for m in members)))
        for score, ci, cores, raw in rows[:args.top]:
            d, imps, names, rules = cands[ci]
            label = f"archetype {a} (k={args.k}, {args.stage})"
            entry = A.layout_entry(d, names, cores, raw, score, label, rules)
            key = f"{d.name}|{label}|{'+'.join(cores)}"
            result["layouts"][key] = entry
            result["archetypes"][a]["best"].append(key)
            print(f"    {score:8.3f}  {d.name:34s} {'+'.join(cores)}")
            for line in entry["grid"]:
                print(f"              {line.replace(' ', '.')}")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
