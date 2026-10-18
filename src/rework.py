"""Deck and core rework (test/core-rework): kernel jobs for the reworked core and implicit rules.

Values are the greater-tier maximum rolls from config.yaml `rework:`. The kernel scores these through its separate
full-recompute path (tagsim.rs `simulate_rework`). Jobs use the build-weighted objective from src/archetype.py:
every stat card is its own stack with its real tags, so tag-gated cores only reach cards that carry the tag.

Card model differences from the live archetype jobs:
  * Stat cards keep their groups; the kernel derives Stat / Evolution / Deluxe / Shiny / Greed / Arcane from the
    card type (Equilibrium and Steadfast key on Stat, Pure on Stat or Evolution, Pluto counts every tag).
  * Colors are real. Decks are mono-color (the color of any color-keyed implicit, else red) unless a rule rewards
    several colors (Equilibrium, Puzzle), in which case every stat card comes in all four colors.
  * Resource / Knack / Temporal cards (0 value, they only feed adjacency rules) are offered when a core or implicit
    reads them.
  * Nitwit (core or Villager deck): Arcane slots take regular cards and get the bonus; Arcane cards are not offered.
"""
from __future__ import annotations

import itertools
from typing import Dict, List, Optional, Sequence, Tuple

from . import archetype as A
from . import simulate as S
from .config import _CFG, Deck
from .implicits import implicits_for_deck
from .simulate import _peers_as_indices

RW = _CFG["rework"]
CORES: Dict[str, float] = dict(RW["cores"])
POOL: List[str] = list(RW["pool"])
GREED_ADDEND: float = float(RW["greed_addend"])
COLORS = ["red", "green", "blue", "yellow"]
BATTERY = {"bazaar": "Resource", "talent": "Knack", "temporal": "Temporal"}


def deck_implicits(deck_keys: Sequence[str], mystery: bool) -> Tuple[List[tuple], float]:
    """Rework kernel implicits for these implicit keys, plus the Villager Nitwit bonus. Mystery decks inherit each
    modifier at `mystery_scale` strength."""
    s = float(RW["mystery_scale"]) if mystery else 1.0
    out, nitwit = [], 0.0
    for key in deck_keys:
        if key == "villager":
            nitwit += float(RW["villager_nitwit"]) * s
            continue
        if key == "shadow":
            out.append(("empty_slots", float(RW["shadow_void"]) * s, [], [], ""))
            continue
        if key == "runic":
            out.append(("mirror", float(RW["runic_tag"]) * s, [], [], ""))
            continue
        for (kind, value, groups, colors, extra) in implicits_for_deck(key):
            if kind == "freq":
                value = 1.0 + s * (value - 1.0)
            else:
                value = value * s
            out.append((kind, value, groups, colors, extra))
    return out, nitwit


def deck_color(imps: List[tuple]) -> str:
    for (_kind, _v, _g, colors, _e) in imps:
        if colors:
            return colors[0]
    return "red"


def legal(cores: Tuple[str, ...], deck: Deck) -> bool:
    if ("archive" in cores or "nitwit" in cores) and not deck.arcane_slots:
        return False
    return not ("archive" in cores and "nitwit" in cores)


def stacks_for(sd: dict, cores: Tuple[str, ...], deck: Deck, imps: List[tuple], nitwit: bool):
    """(stacks, values, attr indices, colors_multi)."""
    attr_idx = {a: i for i, a in enumerate(sd["attrs"])}
    shiny = "shiny" in cores
    multi = "equilibrium" in cores or any(t[0] == "color_mismatch" for t in imps)
    colors = COLORS if multi else [deck_color(imps)]
    stacks, values, attrs = [], [], []

    def add(t, card, color):
        groups = sorted(g for g in card["groups"] if g in A.KERNEL_GROUPS and g != "Stat")
        if shiny:
            groups.append("Foil")
        stacks.append((t, color, "", groups, -1, 0))
        values.append(float(card["value"]))
        attrs.append(attr_idx.get(card["attr"], -1))

    for color in colors:
        for t in A.POSITIONAL:
            for card in sd["cards"].get("evo", []):
                add(t, card, color)
        for card in sd["cards"].get("typeless", []):
            add("typeless", card, color)
        for card in sd["cards"].get("deluxe", []):
            add("deluxe", card, color)
    batteries = {BATTERY[c] for c in cores if c in BATTERY}
    batteries |= {g for (kind, _v, groups, _c, _e) in imps if kind == "adjacency" for g in groups}
    for g in sorted(batteries):
        stacks.append(("typeless", colors[0], "", [g], -1, 0))
        values.append(0.0)
        attrs.append(-1)
    for g in A.GREEDS:
        stacks.append((g, colors[0], "", [], -1, 0))
        values.append(0.0)
        attrs.append(-1)
    if deck.arcane_slots and not nitwit:
        stacks.append(("arcane", colors[0], "", [], -1, 0))
        values.append(0.0)
        attrs.append(-1)
    return stacks, values, attrs, multi


def rework_arg(cores: Tuple[str, ...], deck: Deck, order: dict, nitwit_bonus: float, color: str):
    spec = []
    for c in cores:
        if c == "nitwit":
            continue
        k = int(RW["empyreal_slots"]) if c == "empyreal" else 0
        spec.append((c, float(CORES[c]), k, color if c == "color" else ""))
    nitwit = nitwit_bonus + (float(CORES["nitwit"]) if "nitwit" in cores else 0.0)
    slots = [order[p] for p in deck.arcane_slots] if nitwit > 0 else []
    return (spec, slots, nitwit)


def job(deck: Deck, imp_keys: Sequence[str], mystery: bool, cores: Tuple[str, ...], sd: dict, model, n_iter: int,
        restarts: int, phase2, seed: int):
    slots_list = list(deck.slots)
    order = {p: i for i, p in enumerate(slots_list)}
    imps, nitwit_bonus = deck_implicits(imp_keys, mystery)
    nitwit = nitwit_bonus > 0 or "nitwit" in cores
    stacks, values, attrs, _multi = stacks_for(sd, cores, deck, imps, nitwit)
    n_attr, terms = model
    return S._ndm_core.run_sa_tagged(
        slots=slots_list,
        row_peers=_peers_as_indices(order, deck._row_peers, slots_list),
        col_peers=_peers_as_indices(order, deck._col_peers, slots_list),
        surr_peers=_peers_as_indices(order, deck._surr_peers, slots_list),
        diag_peers=_peers_as_indices(order, deck._diag_peers, slots_list),
        arcane_slot_indices=[] if nitwit else [order[p] for p in deck.arcane_slots],
        stacks=stacks, tag_rules=[], blanket_groups=[], assignable_groups=[],
        implicits=imps, cores=[],
        min_stat_placed=0, final_pass_nonfoil_evo=False, exact_groups=True,
        n_iter=n_iter, restarts=restarts,
        mult_dir_vert=GREED_ADDEND, mult_dir_horiz=GREED_ADDEND,
        mult_pure_base=1.0, mult_pure_scale=0.0, mult_equilibrium=0.0, mult_foil=1.0, mult_steadfast=1.0,
        mult_sparkling=1.0, mult_color=1.0, mult_deluxe_flat=S.MULT_DELUXE_FLAT, mult_deluxe_core_base=1.0,
        mult_deluxe_core_scale=0.0, mult_void_core_base=1.0, mult_void_core_scale=0.0, mult_archive_core=1.0,
        greed_additive=True, additive_cores=True, is_shiny="shiny" in cores, auto_place_arcane=True,
        colors_real=True, complex_cards=False, wv_foil_rules=True, floor_counts_deluxe=False,
        seed=seed, schedule=S._live_sa_schedule(), phase2=phase2,
        value_model=(values, attrs, n_attr, terms),
        rework=rework_arg(cores, deck, order, nitwit_bonus, deck_color(imps)), defer=True,
    )


def pool_for(deck: Deck) -> List[str]:
    return [c for c in POOL if legal((c,), deck)]


def extend(frontier: List[Tuple[str, ...]], deck: Deck, size: int) -> List[Tuple[str, ...]]:
    """Every legal core set of `size` that adds one core to a frontier set (deduplicated, sorted)."""
    out = set()
    for base in frontier:
        for c in pool_for(deck):
            if c in base:
                continue
            s = tuple(sorted(base + (c,)))
            if len(s) == size and legal(s, deck):
                out.add(s)
    return sorted(out)


def all_sets(deck: Deck) -> List[Tuple[str, ...]]:
    pool = pool_for(deck)
    return [s for n in range(1, deck.core_slots + 1) for s in itertools.combinations(pool, n) if legal(s, deck)]
