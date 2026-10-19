"""Check DeckFAST's rework scoring (tagsim.rs simulate_rework) against the ledger's deck fixtures.

The fixtures come from wv-combat-model tools/parity_cases_deck.py (Python reference terms per slot). Each deck is
scored here twice through score_tagged with a one-attribute identity value model: every card worth 1 (the deck's
total of terms) and every card worth its fixture value (the value-weighted total). Both must match the reference to
1e-9 relative.

Run:  uv run --no-sync python scripts/deck_fixture_check.py <deck_fixture.json>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import simulate as S  # noqa: E402

COLORS = ["red", "green", "blue", "yellow"]
TYPES = {0: "row", 1: "col", 2: "surr", 3: "diag", 4: "deluxe", 5: "typeless", 6: "dir_greed_up", 7: "dir_greed_down",
         8: "dir_greed_left", 9: "dir_greed_right", 10: "arcane", 12: "dead"}
KERNEL_GROUPS = ["Offensive", "Defensive", "Physical", "Magical", "Utility", "Resource", "Knack", "Temporal", "Essence",
                 "Foil", "Stat"]
GROUP_BITS = {g: i for i, g in enumerate(KERNEL_GROUPS)}
CORES = ["pure", "equilibrium", "steadfast", "shiny", "color", "premium", "void", "archive", "empyreal", "arsenal",
         "aegis", "tool", "natural", "fae", "jupiter", "pluto", "bazaar", "talent", "temporal", "greed"]
IMP = {0: "global", 1: "freq", 2: "adjacency", 3: "color_mismatch", 4: "row_pos", 5: "chain", 6: "empty_slots",
       7: "unique_groups", 8: "mirror"}
PTYPE = {0: "row", 1: "col", 2: "surr", 3: "diag"}


def peers(slots):
    n = len(slots)
    row = [[j for j in range(n) if j != i and slots[j][0] == slots[i][0]] for i in range(n)]
    col = [[j for j in range(n) if j != i and slots[j][1] == slots[i][1]] for i in range(n)]
    surr = [[j for j in range(n) if j != i and max(abs(slots[j][0] - slots[i][0]), abs(slots[j][1] - slots[i][1])) <= 1]
            for i in range(n)]
    diag = [[j for j in range(n) if j != i and (slots[j][0] - slots[j][1] == slots[i][0] - slots[i][1]
                                               or slots[j][0] + slots[j][1] == slots[i][0] + slots[i][1])]
            for i in range(n)]
    return row, col, surr, diag


def score(d, values):
    slots = [tuple(p) for p in d["slots"]]
    row, col, surr, diag = peers(slots)
    asgn = []
    for t, c, card in zip(d["types"], d["colors"], d["cards"]):
        if t == 12:
            asgn.append(("dead", "", "", []))
            continue
        groups = [g for g in card["groups"] if g in GROUP_BITS and g != "Stat"]
        if d["cset"]["shiny"] and card["kind"] in ("evo", "typeless", "deluxe") and "Foil" not in groups:
            groups.append("Foil")   # src/rework.py stacks_for: under the Shiny core every stat card is Foil
        asgn.append((TYPES[t], COLORS[c], "", groups))
    imps = []
    for (k, v, gm, cm, pt) in d["imps"]:
        kind = IMP[k]
        groups = [g for g, b in GROUP_BITS.items() if gm >> b & 1]
        colors = [COLORS[i] for i in range(4) if cm >> i & 1]
        extra = PTYPE[pt] if kind == "freq" else ("surrounding" if pt == 1 else "column") if kind == "adjacency" else ""
        imps.append((kind, v, groups, colors, extra))
    cs = d["cset"]
    cores = [(name, v, k, "" if col == 255 else COLORS[col]) for (name, v, k, col) in cs["cores"]]
    nslots = [i for i, x in enumerate(cs["nitwit_slots"]) if x]
    n = len(slots)
    curve = (0.0, 1e7, [0.0, 1e7])
    vm = (values, [0] * n, 1, [(1.0, [(0.0, [curve])], [])])
    return S._ndm_core.score_tagged(
        slots=slots, row_peers=row, col_peers=col, surr_peers=surr, diag_peers=diag,
        arcane_slot_indices=[i for i, t in enumerate(d["types"]) if t == 10],
        assignment=asgn, implicits=imps, cores=[],
        mult_dir_vert=d["greed"], mult_dir_horiz=d["greed"], mult_pure_base=1.0, mult_pure_scale=0.0,
        mult_equilibrium=0.0, mult_foil=1.0, mult_steadfast=1.0, mult_sparkling=1.0, mult_color=1.0,
        mult_deluxe_flat=d["deluxe_base"], mult_deluxe_core_base=1.0, mult_deluxe_core_scale=0.0,
        mult_void_core_base=1.0, mult_void_core_scale=0.0, mult_archive_core=1.0,
        greed_additive=True, additive_cores=True, is_shiny=cs["shiny"], colors_real=True, complex_cards=False,
        wv_foil_rules=True, value_model=vm, stack_index=list(range(n)), rework=(cores, nslots, cs["nitwit"]))


def main():
    fx = json.load(open(sys.argv[1], encoding="utf-8"))
    worst, bad = 0.0, 0
    for k, d in enumerate(fx["decks"]):
        ones = [1.0] * len(d["slots"])
        vals = [c["value"] for c in d["cards"]]
        for vv, ref in ((ones, sum(d["terms"])), (vals, sum(t * c["value"] for t, c in zip(d["terms"], d["cards"])))):
            got = score(d, vv)
            err = abs(got - ref) / max(abs(ref), 1.0)
            worst = max(worst, err)
            if err > 1e-9:
                bad += 1
                if bad <= 5:
                    print(f"[deck_fixture_check] deck {k}: DeckFAST {got} reference {ref}")
    print(f"{len(fx['decks'])} decks x 2 totals: {bad} mismatches, worst relative error {worst:.2e}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
