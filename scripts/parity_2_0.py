"""Optimizer 2.0 validation gate (spec §12).

Two checks against the classic 1.x kernel, run in the active --mode:

  A. SCORING equivalence — for sampled (deck, class, cores) combos, score K
     random assignments (drawn from the shared 2.0 vocabulary) with BOTH the
     Python reference ``simulate()`` (the classic spec) and the Rust
     ``score_tagged`` in Max configuration. Must match to 1e-9. This proves
     the scoring math is identical where the models overlap.

  B. OPTIMUM coverage — run the classic SA and the tagged-Max SA (seeded,
     implicits stripped, §6 final pass off) on every deck × class × panel
     config. One-sided: the tagged best may trail the classic best by at
     most TOL_OPT; where it is higher, its layout is re-scored by the Python
     reference and must match to TOL_SCORE. Shiny EQUILIBRIUM core sets are
     excluded (deliberate model split). SA is stochastic, so B is a search
     check; A is the scoring proof.

Run:  uv run python scripts/parity_2_0.py [--mode vanilla] [--fast]
          [--schedule config|geometric|target] [--only-b]

Part B runs every SA restart as a Rust job on all cores (run_sa_jobs).
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import zlib
from pathlib import Path

# Windows consoles default to cp1252 — force UTF-8 so the report glyphs print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --mode is consumed by src.config at import; keep argparse in sync.
_ap = argparse.ArgumentParser()
_ap.add_argument("--mode", default="wolds")
_ap.add_argument("--fast", action="store_true", help="fewer decks / iterations")
_ap.add_argument("--schedule", default="config", choices=["config", "geometric", "target"],
                 help="tagged SA schedule for Part B (geometric = legacy 100 → 0.5)")
_ap.add_argument("--only-b", action="store_true", help="skip Part A")
_ARGS, _ = _ap.parse_known_args()

from src.config import DECKS, MODE, _get_test_configs  # noqa: E402
from src.types import CardClass, CardType, CoreType  # noqa: E402
from src.simulate import (  # noqa: E402
    candidate_cores, run_sa_jobs, sa_optimize, sa_optimize_tagged, simulate,
    _peers_as_indices, _get_placeable,
)
from src import simulate as _sim  # noqa: E402
import ndm_core  # noqa: E402

# Shared 2.0 vocabulary (classic minus the 0-mult greeds, minus wild).
_SHARED = [
    CardType.ROW, CardType.COL, CardType.SURR, CardType.DIAG,
    CardType.TYPELESS, CardType.DELUXE,
    CardType.DIR_GREED_UP, CardType.DIR_GREED_DOWN,
    CardType.DIR_GREED_LEFT, CardType.DIR_GREED_RIGHT,
    CardType.DEAD,
]

TOL_SCORE = 1e-9      # part A: identical math
TOL_OPT   = 5e-3      # part B: max relative shortfall of tagged vs classic


def _random_assignment(deck, card_class, rng):
    placeable = [t for t in _get_placeable(card_class) if t in _SHARED]
    if not placeable:
        placeable = [CardType.TYPELESS, CardType.DEAD]
    asgn = {}
    for p in deck.slots:
        if p in deck.arcane_slots:
            asgn[p] = rng.choice([CardType.ARCANE, CardType.DEAD])
        else:
            asgn[p] = rng.choice(placeable)
    return asgn


def _score_tagged(deck, asgn, card_class, cores):
    slots_list = list(deck.slots)
    slot_order = {p: i for i, p in enumerate(slots_list)}
    mono = "red"
    assignment = []
    for p in slots_list:
        t = asgn[p]
        color = "" if t == CardType.DEAD else mono
        groups = []
        # Run-level foil rule, mirrored from the kernel's materialize():
        # shiny (Wold's) or evo+FOIL-core ⇒ SCORABLE cards are foil. Arcane
        # never takes the Foil bit (materialize strips it — playtest ruling),
        # which matters now that PURE counts by the per-card Foil group.
        foil_core = any(c.value == "foil" for c in cores)
        shiny = card_class == CardClass.SHINY
        wv = MODE != "vanilla"
        foil = (wv if shiny else foil_core)
        scorable = t in (
            CardType.ROW, CardType.COL, CardType.SURR, CardType.DIAG,
            CardType.DELUXE, CardType.TYPELESS,
        )
        if foil and scorable:
            groups.append("Foil")
        assignment.append((t.value, color, "", groups))

    return ndm_core.score_tagged(
        slots               = slots_list,
        row_peers           = _peers_as_indices(slot_order, deck._row_peers, slots_list),
        col_peers           = _peers_as_indices(slot_order, deck._col_peers, slots_list),
        surr_peers          = _peers_as_indices(slot_order, deck._surr_peers, slots_list),
        diag_peers          = _peers_as_indices(slot_order, deck._diag_peers, slots_list),
        arcane_slot_indices = [slot_order[p] for p in deck.arcane_slots],
        assignment          = assignment,
        implicits           = [],
        cores               = [(c.value, "", -1.0) for c in cores],
        mult_dir_vert          = _sim.MULT_DIR_GREED_VERT,
        mult_dir_horiz         = _sim.MULT_DIR_GREED_HORIZ,
        mult_pure_base         = _sim.MULT_PURE_BASE,
        mult_pure_scale        = _sim.MULT_PURE_SCALE,
        mult_equilibrium       = _sim.MULT_EQUILIBRIUM,
        mult_foil              = _sim.MULT_FOIL,
        mult_steadfast         = _sim.MULT_STEADFAST,
        mult_sparkling         = _sim.MULT_SPARKLING,
        mult_color             = _sim.MULT_COLOR,
        mult_deluxe_flat       = _sim.MULT_DELUXE_FLAT,
        mult_deluxe_core_base  = _sim.MULT_DELUXE_CORE_BASE,
        mult_deluxe_core_scale = _sim.MULT_DELUXE_CORE_SCALE,
        mult_void_core_base    = _sim.MULT_VOID_CORE_BASE,
        mult_void_core_scale   = _sim.MULT_VOID_CORE_SCALE,
        mult_archive_core      = _sim.MULT_ARCHIVE_CORE,
        greed_additive         = _sim.GREED_ADDITIVE,
        additive_cores         = _sim.ADDITIVE_CORES,
        is_shiny               = card_class == CardClass.SHINY,
        colors_real            = False,
        complex_cards          = False,
        wv_foil_rules          = MODE != "vanilla",
    )


def part_a(decks, rng, k_per_combo=25):
    print(f"── Part A: scoring equivalence ({len(decks)} decks × classes × cores × {k_per_combo} random assignments)")
    worst = 0.0
    fails = 0
    checked = 0
    for deck in decks:
        for card_class in CardClass:
            for cores in candidate_cores(card_class, deck):
                for _ in range(k_per_combo):
                    asgn = _random_assignment(deck, card_class, rng)
                    py = simulate(deck, asgn, card_class, cores)
                    rs = _score_tagged(deck, asgn, card_class, cores)
                    d = abs(py - rs)
                    rel = d / max(1.0, abs(py))
                    worst = max(worst, rel)
                    checked += 1
                    if rel > TOL_SCORE:
                        fails += 1
                        if fails <= 5:
                            cs = "+".join(sorted(c.value for c in cores))
                            print(f"  ✗ {deck.name} {card_class.value} [{cs}] py={py:.9f} rust={rs:.9f}")
    status = "PASS" if fails == 0 else "FAIL"
    print(f"  Part A: {status} — {checked} assignments, worst rel-Δ {worst:.2e}, {fails} failures")
    return fails == 0


def part_b(decks, n_iter, restarts, t_iter, t_restarts, schedule):
    print(f"── Part B: SA optimum coverage ({len(decks)} decks, classic {n_iter} × {restarts}, "
          f"tagged {t_iter} × {t_restarts}, "
          f"tagged schedule {schedule or 'from config'})")
    # One-sided: the tagged kernel must find at least what the classic kernel
    # finds (within TOL_OPT); it gets the deeper budget, since the check is
    # coverage, not a race. Where it finds MORE, its layout is re-scored by
    # the Python reference simulate() — a match proves the extra score is a
    # legal layout in the shared model, not a scoring divergence.
    # Shiny EQUILIBRIUM is a deliberate model split (5ad35be): the classic
    # kernel prices it as the color-blind upper bound 1 + roll × 4, the
    # tagged kernel runs colors-real and prices it game-exactly. Those core
    # sets can't converge, so they're excluded here.
    # Every restart of both kernels is one seeded Rust job; run_sa_jobs
    # spreads them over all cores.
    combos, jobs, skipped = [], [], 0
    for deck in decks:
        for label, min_reg, max_greed in _get_test_configs(deck):
            cdeck = deck.with_constraints(min_reg, max_greed)
            for card_class in CardClass:
                ci = len(combos)
                combos.append((deck.name, label, card_class, cdeck))
                for cores in candidate_cores(card_class, cdeck):
                    if card_class == CardClass.SHINY and CoreType.EQUILIBRIUM in cores:
                        skipped += 1
                        continue
                    cs = "+".join(sorted(c.value for c in cores))
                    tag = f"{deck.name}|{label}|{card_class.value}|{cs}"
                    for k in range(restarts):
                        jobs.append((ci, 0, cores, sa_optimize(
                            cdeck, card_class, cores, n_iter=n_iter,
                            seed=zlib.crc32(f"{tag}|{k}|classic".encode()), defer=True,
                        )))
                    for k in range(t_restarts):
                        jobs.append((ci, 1, cores, sa_optimize_tagged(
                            cdeck, card_class, cores, n_iter=t_iter,
                            implicits=[], final_pass=False, schedule=schedule,
                            seed=zlib.crc32(f"{tag}|{k}|tagged".encode()), defer=True,
                        )))
    t0 = time.perf_counter()
    outs = run_sa_jobs([j[3] for j in jobs])
    print(f"  {len(jobs)} SA runs in {time.perf_counter() - t0:.1f}s "
          f"({skipped} shiny EQUILIBRIUM core sets excluded)")
    best = [[(-1.0, None, None), (-1.0, None, None)] for _ in combos]
    for (ci, side, cores, _), (asgn, score) in zip(jobs, outs):
        if score > best[ci][side][0]:
            best[ci][side] = (score, cores, asgn)

    lower = mismatch = higher = 0
    for (name, label, card_class, cdeck), (bc, bt) in zip(combos, best):
        best_c, best_t = bc[0], bt[0]
        short = (best_c - best_t) / max(1.0, abs(best_c))
        note, ok = "", True
        if short > TOL_OPT:
            ok, note = False, "  (tagged LOWER)"
            lower += 1
        elif best_t > best_c and bt[2] is not None:
            higher += 1
            slots_list = list(cdeck.slots)
            asgn = {slots_list[i]: CardType(c[0]) for i, c in enumerate(bt[2])}
            ref = simulate(cdeck, asgn, card_class, bt[1])
            d = abs(ref - best_t) / max(1.0, abs(ref))
            if d > TOL_SCORE:
                ok, note = False, f"  (tagged higher, re-score MISMATCH py={ref:.6f})"
                mismatch += 1
            else:
                note = "  (tagged higher, re-scored ✓)"
        print(f"  {'✓' if ok else '✗'} {name:<28s} [{label:>13s}] {card_class.value:<5s} "
              f"classic={best_c:10.3f} tagged={best_t:10.3f} Δ={(best_t - best_c) / max(1.0, abs(best_c)):+.2e}{note}")
    fails = lower + mismatch
    status = "PASS" if fails == 0 else "FAIL"
    print(f"  Part B: {status} — {lower} combos with tagged below classic by > {TOL_OPT}, "
          f"{mismatch} re-score mismatches; {higher} tagged-higher layouts re-scored")
    return fails == 0


def main():
    rng = random.Random(0xDECC)
    decks = list(DECKS)
    if _ARGS.fast:
        decks = decks[:6]
        n_iter, restarts, t_iter, t_restarts, k = 40_000, 4, 80_000, 6, 8
    else:
        n_iter, restarts, t_iter, t_restarts, k = 120_000, 12, 240_000, 16, 25
    schedule = {
        "config": None,
        "geometric": ("geometric", [100.0, 0.5]),
        "target": ("target", [0.5, 0.001, 0.02, 0.01]),
    }[_ARGS.schedule]
    print(f"[parity 2.0] mode={MODE}  decks={len(decks)}")
    t0 = time.perf_counter()
    ok_a = True if _ARGS.only_b else part_a(decks, rng, k_per_combo=k)
    if not _ARGS.only_b:
        print(f"  ({time.perf_counter() - t0:.1f}s)")
    ok_b = part_b(decks, n_iter, restarts, t_iter, t_restarts, schedule)
    if ok_a and ok_b:
        print("\nVALIDATION GATE: PASS")
        sys.exit(0)
    print("\nVALIDATION GATE: FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
