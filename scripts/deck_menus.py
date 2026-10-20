"""Deck menus for the rework ledger: the best deck designs per build group at one stage (src/rework.py rules).

Pipeline for a stage:
  1. Generalist beam over every candidate deck: every single core is screened, then the best `--beam` sets of each
     size are extended by one core, up to the deck's core slots.
  2. Each build group runs the same beam on the generalist's top `--group-decks` decks.
  3. Max only: Mystery decks (the plain deck and the structural Construction v2 build) search their modifier pair.
     Every modifier is screened alone on the generalist with the generalist's top cores, then the best `--pairs`
     pairs run the full beam for every group.
  4. The best `--final` sets per (group, deck) get the full two-phase budget.
  5. Menu: the best design per group plus the generalist's best, then the next-best distinct designs, up to
     `--menu`. Every menu design is scored for every group and every build.

`--regret` adds each build's own best design (its group's top decks and top core sets, re-run for that build alone)
and reports how much of it the menu captures.

Run:  uv run --no-sync python scripts/deck_menus.py <deck_weights.json> <stage> <out.json> [--k 4]
"""
import argparse
import itertools
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


def log(msg):
    print(msg, flush=True)


def candidates(sd, stage, layout_only=False):
    """{label: (deck, implicit keys, mystery, mystery shape or None)} for the non-Mystery decks, and the Mystery
    shapes (label -> deck) when the stage allows them. layout_only: just the stage's fixed ledger layout (its shape
    and implicits, Mystery pairs at the rework strength), re-optimized under the rework rules."""
    out, shapes = {}, {}
    if layout_only:
        entry = sd["layout_entry"]
        shape, _ = A.deck_from_entry(entry)
        out[f"Stage layout ({entry['deck']})"] = (shape, list(entry["implicits"]), "Mystery" in entry["deck"])
        return out, shapes
    for d in DECKS:
        if d.key == "mystery":
            if R.STAGES[stage]["mystery"]:
                shapes["The Mystery Deck"] = d
            continue
        out[d.name] = (d, [d.key] if d.key in DECK_IMPLICITS else [], False)
    if R.STAGES[stage]["mystery"]:
        entry = sd["layout_entry"]
        if "Mystery" in entry["deck"]:
            shape, _ = A.deck_from_entry(entry)
            shapes["The Mystery Deck (Construction v2)"] = shape
        else:
            log(f"[deck_menus][FALLBACK] {stage}: the stage layout is not a Mystery build; no structural Mystery")
    return out, shapes


class Search:
    def __init__(self, sd, stage, models, args):
        self.sd, self.stage, self.models, self.args = sd, stage, models, args
        self.r1, self.n1 = map(int, args.screen.split(","))
        self.scores = {}
        self.jobs = 0
        self.t_screen = 0.0

    def screen(self, keys, decks):
        keys = [k for k in keys if k not in self.scores]
        if not keys:
            return
        jobs = []
        for (g, label, cores) in keys:
            d, imp, mys = decks[label]
            jobs.append(R.job(d, imp, mys, cores, self.sd, self.models[g], self.stage, self.n1, self.r1, None,
                              A.seed_for("menu-s", self.stage, g, label, cores)))
        t0 = time.perf_counter()
        outs = run_sa_jobs(jobs)
        self.t_screen += time.perf_counter() - t0
        self.jobs += len(jobs)
        for k, (_raw, sc) in zip(keys, outs):
            self.scores[k] = sc

    def beam(self, groups, labels, decks, pool=None):
        frontier = {(g, lab): [()] for g in groups for lab in labels}
        top = max(decks[lab][0].core_slots for lab in labels)
        for size in range(1, top + 1):
            keys = []
            for (g, lab) in frontier:
                d = decks[lab][0]
                if size <= d.core_slots:
                    keys += [(g, lab, s) for s in R.extend(frontier[(g, lab)], d, size, pool)]
            self.screen(keys, decks)
            for (g, lab) in frontier:
                mine = sorted(((self.scores[k], k[2]) for k in self.scores
                               if k[0] == g and k[1] == lab and len(k[2]) == size), reverse=True)
                frontier[(g, lab)] = [s for _sc, s in mine[:self.args.beam]]

    def best(self, g, lab, n=1):
        mine = sorted(((v, k[2]) for k, v in self.scores.items() if k[0] == g and k[1] == lab), reverse=True)
        return mine[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights")
    ap.add_argument("stage")
    ap.add_argument("out")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--beam", type=int, default=6)
    ap.add_argument("--group-decks", type=int, default=15)
    ap.add_argument("--pairs", type=int, default=12)
    ap.add_argument("--final", type=int, default=3)
    ap.add_argument("--menu", type=int, default=8)
    ap.add_argument("--per-shape", type=int, default=2, help="menu designs per deck shape beyond the group bests")
    ap.add_argument("--screen", default="2,20000")
    ap.add_argument("--full", default="4,40000")
    ap.add_argument("--regret", action="store_true")
    ap.add_argument("--layout-only", action="store_true", help="only the stage's fixed ledger layout (2x2 deck study)")
    args, _ = ap.parse_known_args()
    stage = args.stage
    t_all = time.perf_counter()

    sd = A.load_stage(args.weights, stage)
    groups = {str(k): v for k, v in A.archetypes(sd, args.k).items()}
    groups["all"] = [b["family"] for b in sd["builds"]]
    models = {g: A.value_terms(sd, m) for g, m in groups.items()}
    decks, shapes = candidates(sd, stage, args.layout_only)
    S = Search(sd, stage, models, args)
    log(f"{stage}: {len(sd['builds'])} builds in {len(groups) - 1} groups "
        f"({', '.join(str(len(v)) for g, v in sorted(groups.items()) if g != 'all')}), {len(decks)} decks, "
        f"greed +{R.greed(stage):.0%}, rolls {R.STAGES[stage]['rolls']}")

    S.beam(["all"], list(decks), decks)
    ranked = sorted(decks, key=lambda lab: -S.best("all", lab)[0][0])
    top = ranked[:args.group_decks]
    sub = [g for g in groups if g != "all"]
    S.beam(sub, top, decks)
    log(f"  beams done: {S.jobs} screens in {S.t_screen:.0f}s")

    if shapes:
        cnt = {}
        for lab in ranked[:10]:
            for c in S.best("all", lab)[0][1]:
                cnt[c] = cnt.get(c, 0) + 1
        core_pool = sorted(cnt, key=lambda c: -cnt[c])[:8]
        for c in R.POOL:
            if len(core_pool) >= 8:
                break
            if c not in core_pool and c not in ("archive", "nitwit"):
                core_pool.append(c)
        mods = R.mystery_pool()
        singles = {}
        for sname, d in shapes.items():
            for m in mods:
                decks[f"{sname} [{m}]"] = (d, [m], True)
        S.beam(["all"], [f"{s} [{m}]" for s in shapes for m in mods], decks, pool=core_pool)
        for m in mods:
            singles[m] = max(S.best("all", f"{s} [{m}]")[0][0] for s in shapes)
        lead = sorted(mods, key=lambda m: -singles[m])
        pairs = sorted(itertools.combinations(lead[:10], 2), key=lambda p: -(singles[p[0]] + singles[p[1]]))
        pairs = pairs[:args.pairs]
        labs = []
        for sname, d in shapes.items():
            for (a, b) in pairs:
                lab = f"{sname} [{a}+{b}]"
                decks[lab] = (d, [a, b], True)
                labs.append(lab)
        S.beam(list(groups), labs, decks)
        log(f"  Mystery: singles led by {', '.join(lead[:5])}; {len(pairs)} pairs x {len(shapes)} shapes; "
            f"{S.jobs} screens total")
        top = top + labs

    r2, n2 = map(int, args.full.split(","))
    full_keys = []
    for g in groups:
        for lab in (ranked if g == "all" else top):
            if not any(k[0] == g and k[1] == lab for k in S.scores):
                continue
            full_keys += [(g, lab, s) for _v, s in S.best(g, lab, args.final)]
    jobs = [R.job(decks[lab][0], decks[lab][1], decks[lab][2], cores, sd, models[g], stage, n2, r2, PHASE2,
                  A.seed_for("menu-f", stage, g, lab, cores)) for (g, lab, cores) in full_keys]
    t0 = time.perf_counter()
    outs = run_sa_jobs(jobs)
    t_full = time.perf_counter() - t0
    full = {k: (raw, sc) for k, (raw, sc) in zip(full_keys, outs)}
    log(f"  full: {len(jobs)} runs in {t_full:.0f}s")

    def best_full(g):
        rows = sorted(((sc, k) for k, (_raw, sc) in full.items() if k[0] == g), reverse=True)
        return rows

    picks, seen, shape_n, shape_scores = [], set(), {}, {}

    def take(k, sig, fill):
        """Add a design. Fill-phase designs must be a new deck/pair, not tie an already-picked design on the same deck
        shape (e.g. Mystery with Cake plus any colour deck on a mono-colour layout), and at most `--per-shape` per
        deck shape."""
        if sig in seen or len(picks) >= args.menu:
            return
        shape = decks[k[1]][0].name
        sc = full[k][1]
        if fill and (shape_n.get(shape, 0) >= args.per_shape
                     or any(abs(sc - x) < 1e-6 for x in shape_scores.get(shape, []))):
            return
        seen.add(sig)
        seen.add(k[1])
        shape_n[shape] = shape_n.get(shape, 0) + 1
        shape_scores.setdefault(shape, []).append(sc)
        picks.append(k)

    # Group bests may share a deck with different cores; extra designs must be new decks (each design carries its
    # top three core sets anyway).
    for g in ["all"] + sorted(sub):
        if best_full(g):
            k = best_full(g)[0][1]
            take(k, (k[1], k[2]), False)
    pool = sorted(((sc, k) for k, (_raw, sc) in full.items()), reverse=True)
    for _sc, k in pool:
        take(k, k[1], True)

    menu = []
    for k in picks:
        g, lab, cores = k
        d, imp, mys = decks[lab]
        raw = full[k][0]
        common, stacks, values, attrs, ident, imps, vb = R.kernel_args(d, imp, mys, cores, sd, stage)
        slots = list(d.slots)
        per_group = {gg: R.score_raw(d, imp, mys, cores, sd, models[gg], stage, raw) for gg in groups}
        per_build = {b["family"]: R.score_raw(d, imp, mys, cores, sd, A.value_terms(sd, [b["family"]]), stage, raw)
                     for b in sd["builds"]}
        alt = [list(s) for _v, s in S.best(g, lab, 3)]
        if list(cores) not in alt:
            alt = [list(cores)] + alt[:2]
        menu.append({
            "label": lab, "deck": d.name, "key": d.key, "found_for": g, "mystery": mys, "implicit_keys": list(imp),
            "core_slots": d.core_slots, "cores": list(cores), "core_sets": alt,
            "core_spec": {"+".join(s): R.core_spec(tuple(s), stage, R.deck_color(imps)) for s in alt},
            "nitwit": {"+".join(s): R.nitwit_value(tuple(s), stage, vb) for s in alt},
            "implicits": [list(t) for t in imps],
            "slots": [list(p) for p in slots],
            "arcane_slots": [list(p) for p in d.arcane_slots],
            "cells": [{"t": c[0], "color": c[1], "groups": list(c[3]),
                       "seed": None if c[4] < 0 else ident[c[4]]} for c in raw],
            "score": full[k][1], "score_by_group": per_group, "score_by_build": per_build,
        })
        log(f"  menu: {full[k][1]:7.3f}  {lab:58s} {'+'.join(cores):34s} (for {g})")

    result = {"stage": stage, "k": args.k, "greed": R.greed(stage), "rolls": R.STAGES[stage]["rolls"],
              "limits": R.STAGES[stage].get("limits") or {}, "deluxe_base": float(R.RW["deluxe_base"]),
              "groups": groups, "menu": menu,
              "timing": {"screen_jobs": S.jobs, "screen_s": S.t_screen, "full_jobs": len(jobs), "full_s": t_full},
              "ranking": {g: [{"label": k[1], "cores": list(k[2]), "score": sc} for sc, k in best_full(g)[:40]]
                          for g in groups}}

    if args.regret:
        t0 = time.perf_counter()
        keys, jobs = [], []
        for b in sd["builds"]:
            fam = b["family"]
            g = next(gg for gg in sub if fam in groups[gg])
            model = A.value_terms(sd, [fam])
            for lab in top:
                for _v, cores in S.best(g, lab, 2):
                    d, imp, mys = decks[lab]
                    keys.append((fam, lab, cores))
                    jobs.append(R.job(d, imp, mys, cores, sd, model, stage, n2, r2, PHASE2,
                                      A.seed_for("menu-r", stage, fam, lab, cores)))
        # Each menu design re-annealed for each build: the best the build can reach on the menu when it may also
        # re-shape the design (a lower bound on the ledger's regret; the fixed-card score is the upper bound).
        n_own = len(jobs)
        for b in sd["builds"]:
            fam = b["family"]
            model = A.value_terms(sd, [fam])
            for mi, m in enumerate(menu):
                k = picks[mi]
                d, imp, mys = decks[k[1]]
                keys.append((fam, "menu", mi))
                jobs.append(R.job(d, imp, mys, k[2], sd, model, stage, n2, r2, PHASE2,
                                  A.seed_for("menu-m", stage, fam, mi)))
        outs = run_sa_jobs(jobs)
        own, menu_re = {}, {}
        for i, (key, (_raw, sc)) in enumerate(zip(keys, outs)):
            fam = key[0]
            own[fam] = max(own.get(fam, -1e9), sc)
            if i >= n_own:
                menu_re[fam] = max(menu_re.get(fam, -1e9), sc)
        reg = {}
        for b in sd["builds"]:
            fam = b["family"]
            fixed = max(m["score_by_build"][fam] for m in menu)
            o = own[fam]
            # In cycles: build values can be negative early, so ratios are meaningless there.
            reg[fam] = {"own_best": o, "menu_fixed": fixed, "menu_reannealed": menu_re[fam],
                        "regret_hi": max(0.0, o - fixed), "regret_lo": max(0.0, o - menu_re[fam])}

        def stat(key):
            vals = [r[key] for r in reg.values()]
            return {"mean": sum(vals) / len(vals), "max": max(vals)}

        hi, lo = stat("regret_hi"), stat("regret_lo")
        result["regret"] = {"per_build": reg, "unit": "cycles", "mean": hi["mean"], "max": hi["max"],
                            "lo_mean": lo["mean"], "lo_max": lo["max"], "jobs": len(jobs), "s": time.perf_counter() - t0}
        log(f"  regret (cycles): fixed cards mean {hi['mean']:.3f} (max {hi['max']:.3f}); re-annealed mean "
            f"{lo['mean']:.3f} (max {lo['max']:.3f}) ({len(jobs)} runs in {result['regret']['s']:.0f}s)")

    result["timing"]["total_s"] = time.perf_counter() - t_all
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    log(f"wrote {args.out} in {result['timing']['total_s']:.0f}s")


if __name__ == "__main__":
    main()
