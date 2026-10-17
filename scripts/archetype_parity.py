"""Archetype-mode parity gate: DeckFAST must reproduce the build ledger's deck totals.

For every build in the ledger's weights file (wv-combat-model tools/deck_weights.py), the build's own deck is
placed on the stage layout and scored through the kernel value model:
  1. per attribute, a probe model whose score is that attribute's deck total must equal the ledger's total;
  2. the build's own two-channel model must return min(damage, survival) at its own deck.

Run:  uv run python scripts/archetype_parity.py <deck_weights.json> [--stages end,max]
"""
import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import archetype as A  # noqa: E402

TOL = 1e-9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("weights")
    ap.add_argument("--stages", default="end,max")
    args, _ = ap.parse_known_args()
    fails = 0
    for stage in args.stages.split(","):
        sd = A.load_stage(args.weights, stage)
        entry = sd["layout_entry"]
        if entry.get("core_values"):
            print(f"  {stage}: layout has core_values {entry['core_values']} (DeckFAST uses config values)")
        deck, imps = A.deck_from_entry(entry)
        cores = tuple(entry["cores"])
        n_attr = len(sd["attrs"])
        worst = 0.0
        for b in sd["builds"]:
            asgn, values, attrs = A.fixed_assignment(deck, entry, b["deck_cards"], sd, cores)
            for i, a in enumerate(sd["attrs"]):
                big = 10.0 * max(sd["xmax"][a], 1.0)
                curves = [(0.0, big, [0.0, big]) if j == i else (0.0, 1.0, []) for j in range(n_attr)]
                got = A.score_fixed(deck, imps, cores, sd, asgn, values, attrs, (n_attr, [(1.0, [(0.0, curves)], [])]))
                want = b["x0"][a]
                rel = abs(got - want) / max(1.0, abs(want))
                worst = max(worst, rel)
                if rel > TOL:
                    fails += 1
                    if fails <= 8:
                        print(f"  ✗ {stage} {b['family']} {a}: DeckFAST {got:.6f} vs ledger {want:.6f}")
            got = A.score_fixed(deck, imps, cores, sd, asgn, values, attrs, A.value_terms(sd, [b["family"]]))
            want = min(b["damage"], b["survival"])
            if abs(got - want) > 1e-6:
                fails += 1
                print(f"  ✗ {stage} {b['family']}: own model {got:.6f} vs min(damage, survival) {want:.6f}")
        print(f"  {stage}: {len(sd['builds'])} builds x {n_attr} attributes, worst total rel-Δ {worst:.2e}")
        if all("cross" in b for b in sd["builds"]):
            placed = {b["family"]: A.fixed_assignment(deck, entry, b["deck_cards"], sd, cores) for b in sd["builds"]}
            errs, pred_all, true_all, same_best = [], [], [], 0
            for b in sd["builds"]:
                model = A.value_terms(sd, [b["family"]], penalties=True)
                own = A.score_fixed(deck, imps, cores, sd, *placed[b["family"]], model)
                preds, trues = {}, {}
                for c, true in b["cross"].items():
                    if true is None:
                        continue
                    preds[c] = A.score_fixed(deck, imps, cores, sd, *placed[c], model) - own
                    trues[c] = true - b["score"]
                    errs.append(abs(preds[c] - trues[c]))
                    pred_all.append(preds[c])
                    true_all.append(trues[c])
                same_best += max(preds, key=preds.get) == max(trues, key=trues.get)
            mp, mt = sum(pred_all) / len(pred_all), sum(true_all) / len(true_all)
            cov = sum((p - mp) * (t - mt) for p, t in zip(pred_all, true_all))
            corr = cov / (sum((p - mp) ** 2 for p in pred_all) * sum((t - mt) ** 2 for t in true_all)) ** 0.5
            errs.sort()
            print(f"  {stage} cross-deck fidelity ({len(errs)} build x deck pairs): corr {corr:.3f}, |err| median "
                  f"{errs[len(errs) // 2]:.3f}, p90 {errs[int(0.9 * len(errs))]:.3f}, max {errs[-1]:.3f} cycles; "
                  f"model picks the true best of the 42 decks for {same_best}/{len(sd['builds'])} builds")
    print("ARCHETYPE PARITY:", "PASS" if fails == 0 else f"FAIL ({fails})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
