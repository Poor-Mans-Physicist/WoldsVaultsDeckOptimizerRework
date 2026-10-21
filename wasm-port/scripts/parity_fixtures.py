"""Score-parity fixtures for the WASM kernel (rework scoring + build-weighted value model).

For a spread of stages, decks, core sets and objectives, runs a short Python search (src/rework.py inputs), scores the
result with the Python kernel, and writes the identical input in the WASM `TagRunInput` shape plus the expected score.
`parity_check.mjs` scores every fixture through the WASM build and compares.

Run (from the repo root, with the Python extension built):
    uv run --no-sync python wasm-port/scripts/parity_fixtures.py <deck_weights.json> <out.json> [--per-stage 12]
"""
import argparse
import itertools
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import archetype as A  # noqa: E402
from src import rework as R  # noqa: E402
from src import simulate as S  # noqa: E402
from src.config import DECKS  # noqa: E402
from src.implicits import DECK_IMPLICITS  # noqa: E402

PY_ONLY = {"cores", "implicits", "rework"}


def wasm_input(common, stacks, rules, vm, raw):
    """The Python kwargs of one job as a WASM TagRunInput, with the fixed assignment and stack indices."""
    inp = {k: v for k, v in common.items() if k not in PY_ONLY}
    inp["cores"] = []
    inp["implicits"] = [{"kind": k, "value": v, "groups": list(g), "colors": list(c), "extra": e}
                        for (k, v, g, c, e) in common["implicits"]]
    core_spec, nit_slots, nit = common["rework"]
    inp["rework"] = {"cores": [list(c) for c in core_spec], "nitwit_slots": list(nit_slots), "nitwit_value": nit}
    inp["stacks"] = [{"t": t, "color": col, "scale_color": sc, "groups": list(g),
                      "count": None if cnt < 0 else cnt, "min_place": mp} for (t, col, sc, g, cnt, mp) in stacks]
    inp["tag_rules"] = [{"axis": a, "key": k, "min": lo, "max": hi} for (a, k, lo, hi) in rules]
    inp.update(blanket_groups=[], assignable_groups=[], min_stat_placed=0, final_pass_nonfoil_evo=False,
               exact_groups=True, n_iter=0, restarts=1, auto_place_arcane=True, floor_counts_deluxe=False)
    if vm is not None:
        values, attrs, n_attr, terms = vm
        inp["value_model"] = {
            "stack_value": values, "stack_attr": attrs, "n_attr": n_attr,
            "terms": [{"weight": w,
                       "channels": [{"base": b, "curves": [[a, {"x0": x0, "dx": dx, "ys": ys}]
                                                           for a, (x0, dx, ys) in enumerate(curves) if ys]}
                                    for (b, curves) in chans],
                       "extra": [[a, {"x0": x0, "dx": dx, "ys": ys}] for a, (x0, dx, ys) in enumerate(extra) if ys]}
                      for (w, chans, extra) in terms]}
    inp["assignment"] = [[c[0], c[1], c[2], list(c[3])] for c in raw]
    inp["stack_index"] = [int(c[4]) for c in raw]
    return inp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights")
    ap.add_argument("out")
    ap.add_argument("--per-stage", type=int, default=12)
    args = ap.parse_args()
    rng = random.Random(20261020)
    fixtures = []
    for stage in ("early", "mid", "end", "max"):
        sd = A.load_stage(args.weights, stage)
        everyone = [b["family"] for b in sd["builds"]]
        model = A.value_terms(sd, everyone)
        decks = [(d, [d.key] if d.key in DECK_IMPLICITS else [], False) for d in DECKS if d.key != "mystery"]
        if R.STAGES[stage]["mystery"]:
            myst = next(d for d in DECKS if d.key == "mystery")
            decks += [(myst, list(p), True) for p in itertools.combinations(R.mystery_pool()[:6], 2)]
        rng.shuffle(decks)
        made = 0
        for d, imp, mys in decks:
            if made >= args.per_stage:
                break
            pool = R.pool_for(d)
            sets = [s for s in R.extend([()], d, 1, pool)]
            if not sets:
                continue
            cores = rng.choice(sets)
            more = [s for s in R.extend([cores], d, 2, pool)] if d.core_slots >= 2 else []
            if more and rng.random() < 0.7:
                cores = rng.choice(more)
            common, stacks, values, attrs, _ident, _imps, _vb = R.kernel_args(d, imp, mys, cores, sd, stage)
            rules = R.tag_rules(stage)
            use_vm = made % 3 != 2
            n_attr, terms = model
            vm = (values, attrs, n_attr, terms) if use_vm else None
            raw, py_score = S._ndm_core.run_sa_tagged(
                **common, stacks=stacks, tag_rules=rules, blanket_groups=[], assignable_groups=[], min_stat_placed=0,
                final_pass_nonfoil_evo=False, exact_groups=True, n_iter=4000, restarts=1, auto_place_arcane=True,
                floor_counts_deluxe=False, seed=1000 + len(fixtures), value_model=vm)
            idx = [c[4] if c[4] >= 0 else 0 for c in raw]
            asgn = [(c[0], c[1], c[2], list(c[3])) for c in raw]
            kw = dict(common, assignment=asgn)
            if vm is not None:
                kw.update(value_model=vm, stack_index=idx)
            scored = S._ndm_core.score_tagged(**kw)
            if abs(scored - py_score) > 1e-9 * max(1.0, abs(py_score)):
                print(f"[parity_fixtures][FALLBACK] {stage} {d.name} {cores}: run score {py_score} != rescored "
                      f"{scored}; the rescored value is the reference", flush=True)
            fixtures.append({"stage": stage, "deck": d.name, "implicits": imp, "mystery": mys, "cores": list(cores),
                             "objective": "value" if use_vm else "ndm", "expected": scored,
                             "input": wasm_input(common, stacks, rules, vm, raw)})
            made += 1
        print(f"{stage}: {made} fixtures", flush=True)
    Path(args.out).write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")
    print(f"wrote {len(fixtures)} fixtures to {args.out}")


if __name__ == "__main__":
    main()
