"""Entry point: the solve() orchestrator (all SA runs as Rust jobs) + CLI."""
from __future__ import annotations

import os
import sys
import time
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Windows pipes/consoles default to cp1252, which can't encode the ✓/█ the
# progress prints use.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from .types import CardClass, CardType
from .config import (
    DECKS,
    EXPORT_SPREADSHEET,
    IMPLICITS_ENABLED,
    MODE,
    SPREADSHEET_PREFIX,
    STRUCTURAL_IMPLICITS,
    Deck,
    _CFG,
    _get_test_configs,
)
from .simulate import candidate_cores, run_sa_jobs, sa_optimize, sa_optimize_tagged
from .implicits import implicits_for_deck
from .report import _report, generate_spreadsheet

# Engine selection (Optimizer 2.0). "tagged" drives the same tag-aware Max
# kernel the web app uses (deck implicits included on Wold's); "classic"
# keeps the 1.x color-blind kernel for A/B comparison.
ENGINE: str = str(_CFG.get("engine", "tagged"))


# ──────────────────────────────────────────────────────────────────────────────
# Top-level optimizer
# ──────────────────────────────────────────────────────────────────────────────

def deck_implicits(deck: Deck) -> list:
    """Implicit tuples the tagged kernel scores for this deck."""
    # Deck implicits are a Wold's-only mechanic; the tagged engine evaluates
    # them inside the kernel. Classic engine ignores them (pre-2.0 behavior).
    # IMPLICITS_ENABLED (config `implicits.enabled` / --no-implicits) scores
    # bare layouts instead — the base-vs-implicit comparison switch.
    implicits = (
        implicits_for_deck(deck.key)
        if MODE != "vanilla" and IMPLICITS_ENABLED else []
    )
    # Structural balance layouts may force a pair (Mystery → runic + bishop):
    # each forced key resolves through the same per-deck implicit catalog.
    forced = STRUCTURAL_IMPLICITS.get(deck.key)
    if forced and MODE != "vanilla" and IMPLICITS_ENABLED:
        implicits = [t for k in forced for t in implicits_for_deck(k)]
    return implicits


@dataclass
class Task:
    """One spreadsheet cell: a constrained deck × card class."""
    deck:      Deck
    cls:       CardClass
    implicits: list


def _search_cfg() -> dict:
    """Search budget from config.yaml `testing` (phase2 / halving optional)."""
    t = _CFG["testing"]
    p2 = t.get("phase2")
    hv = t.get("halving") or {}
    return {
        "n_iter":   int(t["n_iter"]),
        "restarts": int(t["restarts"]),
        "phase2":   (int(p2["top_k"]), int(p2["n_iter"]), str(p2["schedule"]["kind"]),
                     [float(v) for v in p2["schedule"]["params"]]) if p2 else None,
        "halving":  hv if hv.get("enabled") else None,
        "strata":   ((list(st["axes"]), int(st["span"]), int(st["n_iter"]))
                     if (st := t.get("strata") or {}).get("enabled") else None),
    }


def _seed(salt: Optional[str], *parts) -> Optional[int]:
    return zlib.crc32("|".join(map(str, (salt,) + parts)).encode()) if salt else None


def _layout(deck: Deck, raw) -> dict:
    """Kernel assignment (tagged tuples or classic strings) → {pos: CardType}."""
    slots = list(deck.slots)
    return {slots[i]: CardType(c if isinstance(c, str) else c[0]) for i, c in enumerate(raw)}


def solve(tasks: List[Task], search: Optional[dict] = None, seed_salt: Optional[str] = None,
          log=print) -> List[dict]:
    """Best layout per task over every candidate core set, all SA runs as
    Rust jobs on every core (run_sa_jobs).

    Tagged engine: one job per core set runs all its restarts plus the
    optional phase-2 re-anneal. With halving, every core set is first
    screened on a small budget; only those within `margin` of the task's
    leader (and at least the top `keep_min`) get the full budget. Classic
    engine: one job per restart, no phase 2 / halving.
    """
    sc = search or _search_cfg()
    cands = [candidate_cores(t.cls, t.deck) for t in tasks]
    best = [{"score": -1.0} for _ in tasks]

    def run(entries, label):
        """entries: (task index, cores, job) → folds results into `best`."""
        t0 = time.perf_counter()
        outs = run_sa_jobs([e[2] for e in entries])
        log(f"  {label}: {len(entries)} jobs in {time.perf_counter() - t0:.1f}s")
        scores = {}
        for (ti, cores, _), (raw, score) in zip(entries, outs):
            scores[(ti, cores)] = max(scores.get((ti, cores), -1.0), score)
            if score > best[ti]["score"]:
                best[ti] = {"score": score, "cores": cores, "assignment": _layout(tasks[ti].deck, raw)}
        return scores

    if ENGINE == "classic":
        run([(ti, cores, sa_optimize(t.deck, t.cls, cores, n_iter=sc["n_iter"],
                                     seed=_seed(seed_salt, ti, cores, k), defer=True))
             for ti, t in enumerate(tasks) for cores in cands[ti] for k in range(sc["restarts"])],
            "classic")
        return best

    def tagged(ti, cores, n_iter, restarts, phase2, tag):
        t = tasks[ti]
        return (ti, cores, sa_optimize_tagged(
            t.deck, t.cls, cores, n_iter=n_iter, implicits=t.implicits, restarts=restarts,
            phase2=phase2, strata=sc.get("strata") if phase2 else None,
            seed=_seed(seed_salt, tag, ti, sorted(c.value for c in cores)),
            defer=True,
        ))

    survivors = [list(c) for c in cands]
    hv = sc["halving"]
    if hv:
        screen = run([tagged(ti, cores, int(hv["n_iter"]), int(hv["restarts"]), None, "screen")
                      for ti in range(len(tasks)) for cores in cands[ti]], "screen")
        for ti in range(len(tasks)):
            ranked = sorted(cands[ti], key=lambda c: -screen[(ti, c)])
            lead = screen[(ti, ranked[0])] if ranked else 0.0
            keep = [c for c in ranked if screen[(ti, c)] >= (1.0 - float(hv["margin"])) * lead]
            survivors[ti] = ranked[:max(len(keep), int(hv["keep_min"]))]
        n_all, n_kept = sum(map(len, cands)), sum(map(len, survivors))
        log(f"  halving kept {n_kept}/{n_all} core sets")
    run([tagged(ti, cores, sc["n_iter"], sc["restarts"], sc["phase2"], "full")
         for ti in range(len(tasks)) for cores in survivors[ti]], "full")
    return best


def optimize(
    deck:     Deck,
    n_iter:   Optional[int] = None,
    restarts: Optional[int] = None,
    verbose:  bool = True,
) -> Dict[CardClass, dict]:
    """Best layout per card class for one (already constrained) deck."""
    sc = _search_cfg()
    if n_iter is not None:
        sc["n_iter"] = n_iter
    if restarts is not None:
        sc["restarts"] = restarts
    tasks = [Task(deck, cls, deck_implicits(deck)) for cls in CardClass]
    best = solve(tasks, sc, log=print if verbose else (lambda *_: None))
    results = {t.cls: b for t, b in zip(tasks, best)}
    if verbose:
        for cls, b in results.items():
            _report(cls, b, deck)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def panel_tasks(decks) -> List[Tuple[str, str, Task]]:
    """(deck name, panel label, task) for every deck × panel config × class."""
    out = []
    for deck in decks:
        imps = deck_implicits(deck)
        for label, min_reg, max_greed in _get_test_configs(deck):
            cdeck = deck.with_constraints(min_reg, max_greed)
            for cls in CardClass:
                out.append((deck.name, label, Task(cdeck, cls, imps)))
    return out


def main() -> None:
    print(f"[ndm] Mode: {MODE}  engine: {ENGINE}")
    t0 = time.perf_counter()
    cells = panel_tasks(DECKS)
    print(f"Solving {len(cells)} deck × panel × class cells on all cores...")
    best = solve([c[2] for c in cells])
    print(f"  done in {time.perf_counter() - t0:.1f}s")

    all_results: Dict[str, Dict[str, Dict[CardClass, dict]]] = {}
    for (name, label, task), b in zip(cells, best):
        all_results.setdefault(name, {}).setdefault(label, {})[task.cls] = b

    print("\n" + "█" * 66)
    print("  FINAL SUMMARY — ALL DECKS")
    print("█" * 66)
    for deck in DECKS:
        print(f"\n  {deck.name}")
        for label, _, __ in _get_test_configs(deck):
            test_res = all_results[deck.name].get(label, {})
            parts    = []
            for card_class in CardClass:
                ndm = test_res[card_class]["score"] if card_class in test_res else float("nan")
                parts.append(f"{card_class.value.upper()}={ndm:.2f}")
            print(f"    [{label:>13s}]  {'   '.join(parts)}")

    if EXPORT_SPREADSHEET:
        filename = f"{SPREADSHEET_PREFIX}ndm_simulation.xlsx"
        if os.path.exists(filename):
            print(f"\n  Spreadsheet '{filename}' already exists — skipping export.")
        else:
            generate_spreadsheet(all_results, DECKS, filename)
