"""Build-weighted deck optimization (archetype mode).

Instead of NDM, a deck is scored by what it is worth to a group of builds from the Wold's Vaults build ledger
(wv-combat-model tools/deck_weights.py). Every stat card is its own stack, with the card's real tags and its value at
the stage tier, so tag-gated multipliers only reach cards that carry the tag. A placed card adds slot multiplier x
value to its attribute's deck total; each member build turns those totals into min(damage cycles, survival cycles)
plus threshold penalties through its probed curves; the deck's score is the members' mean.

Tag cores (Arsenal/Aegis/Tool/Natural/Fae) are additive per-group bonuses, passed to the kernel as global implicits.
"""
from __future__ import annotations

import itertools
import json
import zlib
from typing import Dict, List, Optional, Tuple

from . import simulate as S
from .config import _CFG, Deck
from .implicits import implicits_for_deck
from .simulate import _peers_as_indices

KERNEL_GROUPS = {"Offensive", "Defensive", "Physical", "Magical", "Utility", "Resource", "Knack", "Temporal",
                 "Essence", "Foil", "Stat"}
TAG_CORES = {"arsenal": "Offensive", "aegis": "Defensive", "tool": "Utility", "natural": "Physical", "fae": "Magical"}
BASE_CORES = ["pure", "color", "foil", "deluxe_core", "void_core", "archive_core"]
POSITIONAL = ["row", "col", "surr", "diag"]
GREEDS = ["dir_greed_up", "dir_greed_down", "dir_greed_left", "dir_greed_right"]
CHAR_TYPE = {"R": "row", "C": "col", "S": "surr", "X": "diag", "D": "deluxe", "T": "typeless", "a": "arcane",
             "^": "dir_greed_up", "v": "dir_greed_down", "<": "dir_greed_left", ">": "dir_greed_right"}
TYPE_CHAR = {v: k for k, v in CHAR_TYPE.items()}


def tag_core_value() -> float:
    return float((_CFG.get("cores") or {}).get("tag_core", 1.25))


def load_stage(path: str, stage: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["stages"][stage]


def archetypes(sd: dict, k: int) -> Dict[int, List[str]]:
    """{archetype index: member families} for a k-way clustering in the weights file."""
    out: Dict[int, List[str]] = {}
    for fam, lab in sd["clusters"][str(k)]["labels"].items():
        out.setdefault(lab, []).append(fam)
    return out


def value_terms(sd: dict, members: List[str], penalties: bool = False):
    """Kernel value-model terms for these member builds: per build, damage and survival channels (base = the build's
    cycles, curves = cycle change vs deck total). Each curve keeps the build's own probe grid, so its operating point
    is a grid node.

    `penalties` adds extra curves for the score change the channels don't explain: the mana-regen / CDR / movement
    requirement penalties. They are off by default: the probes hold gear fixed, so a deck that drops a requirement stat
    looks catastrophic, while a real re-anneal moves that stat onto gear (tools/deck_study.py: one shared deck serves
    every build). The objective therefore assumes gear covers the requirements."""
    attrs = sd["attrs"]
    builds = [b for b in sd["builds"] if b["family"] in set(members)]
    if len(builds) != len(members):
        raise SystemExit(f"archetype: {len(members) - len(builds)} member builds missing from the weights file")
    terms = []
    for b in builds:
        d0, s0 = b["damage"], b["survival"]
        dch, sch, extra = [], [], []
        for a in attrs:
            pts = sorted(b["curves"].get(a) or [])
            if len(pts) < 2:
                dch.append((0.0, 1.0, []))
                sch.append((0.0, 1.0, []))
                extra.append((0.0, 1.0, []))
                continue
            x0, dx = pts[0][0], pts[1][0] - pts[0][0]
            dd = [p[1] for p in pts]
            ss = [p[2] for p in pts]
            ex = [p[3] - (min(d0 + p[1], s0 + p[2]) - min(d0, s0)) for p in pts]
            dch.append((x0, dx, dd))
            sch.append((x0, dx, ss))
            extra.append((x0, dx, ex))
        terms.append((1.0 / len(builds), [(d0, dch), (s0, sch)], extra if penalties else []))
    return len(attrs), terms


def stacks_for(sd: dict, cores: Tuple[str, ...], deck: Deck, allow_deluxe: bool = True):
    """(stacks, values, attr indices): one stack per (slot type, stat card) with the card's real tags; greed and
    arcane stacks carry no stat. Under a Foil core every stat card is Foil (Wold's run-level rule)."""
    attr_idx = {a: i for i, a in enumerate(sd["attrs"])}
    foil = "foil" in cores
    stacks, values, attrs = [], [], []

    def add(t, card):
        groups = sorted(g for g in card["groups"] if g in KERNEL_GROUPS and g != "Stat")
        if foil:
            groups.append("Foil")
        stacks.append((t, "red", "", groups, -1, 0))
        values.append(float(card["value"]))
        attrs.append(attr_idx.get(card["attr"], -1))

    for t in POSITIONAL:
        for card in sd["cards"].get("evo", []):
            add(t, card)
    for card in sd["cards"].get("typeless", []):
        add("typeless", card)
    if allow_deluxe:
        for card in sd["cards"].get("deluxe", []):
            add("deluxe", card)
    for g in GREEDS:
        stacks.append((g, "red", "", [], -1, 0))
        values.append(0.0)
        attrs.append(-1)
    if deck.arcane_slots:
        stacks.append(("arcane", "red", "", [], -1, 0))
        values.append(0.0)
        attrs.append(-1)
    return stacks, values, attrs


def core_sets(deck: Deck, allow_deluxe: bool = True) -> List[Tuple[str, ...]]:
    """Every core combination of size 1..core_slots from the EVO base cores plus the tag cores."""
    pool = [c for c in BASE_CORES if (c != "archive_core" or deck.arcane_slots) and (c != "deluxe_core" or allow_deluxe)]
    pool += list(TAG_CORES)
    out = []
    for n in range(1, deck.core_slots + 1):
        out += list(itertools.combinations(pool, n))
    return out


def deck_from_entry(entry: dict) -> Tuple[Deck, List[tuple]]:
    """A DeckFAST Deck with the exact shape of a ledger layout entry (grid), plus its kernel implicits."""
    slots, arcane = set(), set()
    for r, line in enumerate(entry["grid"]):
        for c, ch in enumerate(line):
            if ch != " ":
                slots.add((r, c))
                if ch == "a":
                    arcane.add((r, c))
    d = Deck(slots=slots, core_slots=entry["core_slots"],
             arcane_slots=arcane, name=f"{entry['deck']} (stage shape)", key=entry["key"])
    imps = [t for name in entry["implicits"] for t in implicits_for_deck(name)]
    return d, imps


def job(deck: Deck, imps: List[tuple], cores: Tuple[str, ...], sd: dict, model, n_iter: int, restarts: int,
        phase2, seed: int, allow_deluxe: bool = True):
    """A deferred kernel job (run with simulate.run_sa_jobs)."""
    slots_list = list(deck.slots)
    order = {p: i for i, p in enumerate(slots_list)}
    stacks, values, attrs = stacks_for(sd, cores, deck, allow_deluxe)
    n_attr, terms = model
    tag = [("global", tag_core_value(), [TAG_CORES[c]], [], "") for c in cores if c in TAG_CORES]
    greed = sd["greed_addend"] if sd["greed_addend"] is not None else 0.0
    return S._ndm_core.run_sa_tagged(
        slots=slots_list,
        row_peers=_peers_as_indices(order, deck._row_peers, slots_list),
        col_peers=_peers_as_indices(order, deck._col_peers, slots_list),
        surr_peers=_peers_as_indices(order, deck._surr_peers, slots_list),
        diag_peers=_peers_as_indices(order, deck._diag_peers, slots_list),
        arcane_slot_indices=[order[p] for p in deck.arcane_slots],
        stacks=stacks, tag_rules=[], blanket_groups=[], assignable_groups=[],
        implicits=imps + tag,
        cores=[(c, "", -1.0) for c in cores if c not in TAG_CORES],
        min_stat_placed=0, final_pass_nonfoil_evo=False, exact_groups=True,
        n_iter=n_iter, restarts=restarts,
        mult_dir_vert=greed, mult_dir_horiz=greed,
        mult_pure_base=S.MULT_PURE_BASE, mult_pure_scale=S.MULT_PURE_SCALE, mult_equilibrium=S.MULT_EQUILIBRIUM,
        mult_foil=S.MULT_FOIL, mult_steadfast=S.MULT_STEADFAST, mult_sparkling=S.MULT_SPARKLING,
        mult_color=S.MULT_COLOR, mult_deluxe_flat=S.MULT_DELUXE_FLAT, mult_deluxe_core_base=S.MULT_DELUXE_CORE_BASE,
        mult_deluxe_core_scale=S.MULT_DELUXE_CORE_SCALE, mult_void_core_base=S.MULT_VOID_CORE_BASE,
        mult_void_core_scale=S.MULT_VOID_CORE_SCALE, mult_archive_core=S.MULT_ARCHIVE_CORE,
        greed_additive=S.GREED_ADDITIVE, additive_cores=True, is_shiny=False, auto_place_arcane=True,
        colors_real=False, complex_cards=False, wv_foil_rules=True, floor_counts_deluxe=False,
        seed=seed, schedule=S._live_sa_schedule(), phase2=phase2,
        value_model=(values, attrs, n_attr, terms), defer=True,
    )


def layout_entry(deck: Deck, imps_names: List[str], cores: Tuple[str, ...], raw, score: float, label: str,
                 implicit_rules: dict) -> dict:
    """Ledger layout entry (grid of slot types) plus the stat plan per slot."""
    slots_list = list(deck.slots)
    rmin = min(r for r, _ in slots_list)
    cmin = min(c for _, c in slots_list)
    rmax = max(r for r, _ in slots_list)
    cmax = max(c for _, c in slots_list)
    cells = {p: "_" for p in slots_list}
    for p, card in zip(slots_list, raw):
        if card[0] != "dead":
            cells[p] = TYPE_CHAR[card[0]]
    grid = ["".join(cells.get((r, c), " ") for c in range(cmin, cmax + 1)) for r in range(rmin, rmax + 1)]
    return {"deck": deck.name, "key": deck.key, "config": label, "score_archetype": score, "cores": list(cores),
            "implicits": imps_names, "implicit_rules": implicit_rules,
            "core_slots": sum(1 for c in cores if c in BASE_CORES or c in TAG_CORES), "grid": grid}


def seed_for(*parts) -> int:
    return zlib.crc32("|".join(map(str, parts)).encode())


SLOT_FAMILY = {"row": "evo", "col": "evo", "surr": "evo", "diag": "evo", "typeless": "typeless", "deluxe": "deluxe"}


def fixed_assignment(deck: Deck, entry: dict, deck_cards, sd: dict, cores: Tuple[str, ...]):
    """A ledger build's deck as kernel placements: (assignment, per-slot value, per-slot attr index)."""
    attr_idx = {a: i for i, a in enumerate(sd["attrs"])}
    chars = {(r, c): ch for r, line in enumerate(entry["grid"]) for c, ch in enumerate(line) if ch != " "}
    card_at = {(r, c): cid for r, c, cid in deck_cards}
    by_id = {fam: {cd["id"]: cd for cd in cards} for fam, cards in sd["cards"].items()}
    foil = "foil" in cores
    asgn, values, attrs = [], [], []
    for p in list(deck.slots):
        ch = chars[p]
        if ch == "_":
            asgn.append(("dead", "", "", []))
            values.append(0.0)
            attrs.append(-1)
            continue
        t = CHAR_TYPE[ch]
        if t in SLOT_FAMILY:
            card = by_id[SLOT_FAMILY[t]][card_at[p]]
            groups = sorted(g for g in card["groups"] if g in KERNEL_GROUPS and g != "Stat") + (["Foil"] if foil else [])
            asgn.append((t, "red", "", groups))
            values.append(float(card["value"]))
            attrs.append(attr_idx[card["attr"]])
        else:
            asgn.append((t, "red", "", []))
            values.append(0.0)
            attrs.append(-1)
    return asgn, values, attrs


def score_fixed(deck: Deck, imps: List[tuple], cores: Tuple[str, ...], sd: dict, asgn, values, attrs, model) -> float:
    """Value-model score of a fixed placement (kernel score_tagged)."""
    slots_list = list(deck.slots)
    order = {p: i for i, p in enumerate(slots_list)}
    n_attr, terms = model
    tag = [("global", tag_core_value(), [TAG_CORES[c]], [], "") for c in cores if c in TAG_CORES]
    greed = sd["greed_addend"] if sd["greed_addend"] is not None else 0.0
    return S._ndm_core.score_tagged(
        slots=slots_list,
        row_peers=_peers_as_indices(order, deck._row_peers, slots_list),
        col_peers=_peers_as_indices(order, deck._col_peers, slots_list),
        surr_peers=_peers_as_indices(order, deck._surr_peers, slots_list),
        diag_peers=_peers_as_indices(order, deck._diag_peers, slots_list),
        arcane_slot_indices=[order[p] for p in deck.arcane_slots],
        assignment=asgn, implicits=imps + tag, cores=[(c, "", -1.0) for c in cores if c not in TAG_CORES],
        mult_dir_vert=greed, mult_dir_horiz=greed,
        mult_pure_base=S.MULT_PURE_BASE, mult_pure_scale=S.MULT_PURE_SCALE, mult_equilibrium=S.MULT_EQUILIBRIUM,
        mult_foil=S.MULT_FOIL, mult_steadfast=S.MULT_STEADFAST, mult_sparkling=S.MULT_SPARKLING,
        mult_color=S.MULT_COLOR, mult_deluxe_flat=S.MULT_DELUXE_FLAT, mult_deluxe_core_base=S.MULT_DELUXE_CORE_BASE,
        mult_deluxe_core_scale=S.MULT_DELUXE_CORE_SCALE, mult_void_core_base=S.MULT_VOID_CORE_BASE,
        mult_void_core_scale=S.MULT_VOID_CORE_SCALE, mult_archive_core=S.MULT_ARCHIVE_CORE,
        greed_additive=S.GREED_ADDITIVE, additive_cores=True, is_shiny=False,
        colors_real=False, complex_cards=False, wv_foil_rules=True,
        value_model=(values, attrs, n_attr, terms), stack_index=list(range(len(slots_list))),
    )
