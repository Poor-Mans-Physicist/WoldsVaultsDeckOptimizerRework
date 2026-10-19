"""Deck and core rework (test/core-rework): kernel jobs for the reworked core and implicit rules.

The kernel scores these through its separate full-recompute path (tagsim.rs `simulate_rework`). Jobs use the
build-weighted objective from src/archetype.py: every stat card is its own stack with its real tags, so tag-gated
cores only reach cards that carry the tag.

Card model differences from the live archetype jobs:
  * Stat cards keep their groups; the kernel derives Stat / Evolution / Deluxe / Shiny / Greed / Arcane from the
    card type (Equilibrium and Steadfast key on Stat, Pure on Stat or Evolution, Pluto counts every tag).
  * Colors are real. Decks are mono-color (the color of any color-keyed implicit, else red) unless a rule rewards
    several colors (Equilibrium, Puzzle), in which case every stat card comes in all four colors.
  * Resource / Knack / Temporal cards (0 value, they only feed adjacency rules) are offered when a core or implicit
    reads them.
  * Nitwit (core or Villager deck): Arcane slots take regular cards and get the bonus; Arcane cards are not offered.

Stage settings (config.yaml `rework.stages`): greed card bonus, which core roll the optimizer uses, card limits and
whether Mystery decks are allowed.
"""
from __future__ import annotations

import itertools
from typing import Dict, List, Sequence, Tuple

from . import archetype as A
from . import simulate as S
from .config import _CFG, Deck
from .implicits import DECK_IMPLICITS, implicits_for_deck
from .simulate import _peers_as_indices

RW = _CFG["rework"]
POOL: List[str] = list(RW["pool"])
STAGES: Dict[str, dict] = dict(RW["stages"])
COLORS = ["red", "green", "blue", "yellow"]
BATTERY = {"bazaar": "Resource", "talent": "Knack", "temporal": "Temporal"}
NO_EFFECT = {"gameplay", "mystery"}


def roll(core: str, stage: str) -> float:
    """The core's roll the optimizer uses at this stage (multiplicative cores as the bonus: x1.5 -> 0.5)."""
    tier = STAGES[stage]["rolls"]
    r = RW["rolls"][core]
    if tier == "normal_mid":
        return 0.5 * (r["normal"][0] + r["normal"][1])
    if tier == "normal_top":
        return float(r["normal"][1])
    if tier == "greater_top":
        return float(r["greater"][1])
    raise SystemExit(f"rework: unknown roll tier {tier!r} for stage {stage}")


def empyreal_slots(stage: str) -> int:
    return int(RW["empyreal_slots"]["greater" if STAGES[stage]["rolls"].startswith("greater") else "normal"])


def greed(stage: str) -> float:
    return float(STAGES[stage]["greed"])


def mystery_pool() -> List[str]:
    """Modifier keys a Mystery deck can roll that change card values (gameplay-only ones are skipped)."""
    out = []
    for key, e in sorted(DECK_IMPLICITS.items()):
        if e.get("kind") in NO_EFFECT and key != "villager":
            continue
        out.append(key)
    return out


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
            value = 1.0 + s * (value - 1.0) if kind == "freq" else value * s
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


def stacks_for(sd: dict, cores: Tuple[str, ...], deck: Deck, imps: List[tuple], nitwit: bool, stage: str):
    """(stacks, values, attr indices, per-stack identity) for one job. Identity: {"kind", "id"} where kind is
    evo / typeless / deluxe (id = card id), battery (id = group), greed (id = direction) or arcane."""
    attr_idx = {a: i for i, a in enumerate(sd["attrs"])}
    shiny = "shiny" in cores
    multi = "equilibrium" in cores or any(t[0] == "color_mismatch" for t in imps)
    colors = COLORS if multi else [deck_color(imps)]
    limits = STAGES[stage].get("limits") or {}
    stacks, values, attrs, ident = [], [], [], []

    def add(t, fam, card, color):
        groups = sorted(g for g in card["groups"] if g in A.KERNEL_GROUPS and g != "Stat")
        if shiny:
            groups.append("Foil")
        stacks.append((t, color, "", groups, -1, 0))
        values.append(float(card["value"]))
        attrs.append(attr_idx.get(card["attr"], -1))
        ident.append({"kind": fam, "id": card["id"], "color": color})

    for color in colors:
        for t in A.POSITIONAL:
            for card in sd["cards"].get("evo", []):
                add(t, "evo", card, color)
        for card in sd["cards"].get("typeless", []):
            add("typeless", "typeless", card, color)
        for card in sd["cards"].get("deluxe", []):
            add("deluxe", "deluxe", card, color)
    batteries = {BATTERY[c] for c in cores if c in BATTERY}
    batteries |= {g for (kind, _v, groups, _c, _e) in imps if kind == "adjacency" for g in groups}
    for g in sorted(batteries):
        cap = limits.get(g.lower())
        stacks.append(("typeless", colors[0], "", [g], -1 if cap is None else int(cap), 0))
        values.append(0.0)
        attrs.append(-1)
        ident.append({"kind": "battery", "id": g, "color": colors[0]})
    for g in A.GREEDS:
        stacks.append((g, colors[0], "", [], -1, 0))
        values.append(0.0)
        attrs.append(-1)
        ident.append({"kind": "greed", "id": g, "color": colors[0]})
    if deck.arcane_slots and not nitwit:
        stacks.append(("arcane", colors[0], "", [], -1, 0))
        values.append(0.0)
        attrs.append(-1)
        ident.append({"kind": "arcane", "id": "arcane", "color": colors[0]})
    return stacks, values, attrs, ident


def tag_rules(stage: str) -> List[tuple]:
    """Card limits as kernel tag rules (each limit is its own cap)."""
    lim = STAGES[stage].get("limits") or {}
    rules = []
    if "deluxe" in lim:
        rules.append(("type", "deluxe", 0, int(lim["deluxe"])))
    for g in ("knack", "temporal", "resource"):
        if g in lim:
            rules.append(("group", g.capitalize(), 0, int(lim[g])))
    return rules


def core_spec(cores: Tuple[str, ...], stage: str, color: str) -> List[tuple]:
    """Resolved core list (name, value, k, color) for the kernel and the ledger menu."""
    return [(c, roll(c, stage), empyreal_slots(stage) if c == "empyreal" else 0, color if c == "color" else "")
            for c in cores if c != "nitwit"]


def nitwit_value(cores: Tuple[str, ...], stage: str, villager_bonus: float) -> float:
    return villager_bonus + (roll("nitwit", stage) if "nitwit" in cores else 0.0)


def rework_arg(cores, deck, order, villager_bonus, color, stage):
    nit = nitwit_value(cores, stage, villager_bonus)
    slots = [order[p] for p in deck.arcane_slots] if nit > 0 else []
    return (core_spec(cores, stage, color), slots, nit)


def kernel_args(deck: Deck, imp_keys: Sequence[str], mystery: bool, cores: Tuple[str, ...], sd: dict, stage: str):
    """Everything a run or a fixed-assignment score needs for one (deck, implicits, cores, stage)."""
    slots_list = list(deck.slots)
    order = {p: i for i, p in enumerate(slots_list)}
    imps, villager_bonus = deck_implicits(imp_keys, mystery)
    nitwit = villager_bonus > 0 or "nitwit" in cores
    stacks, values, attrs, ident = stacks_for(sd, cores, deck, imps, nitwit, stage)
    g = greed(stage)
    common = dict(
        slots=slots_list,
        row_peers=_peers_as_indices(order, deck._row_peers, slots_list),
        col_peers=_peers_as_indices(order, deck._col_peers, slots_list),
        surr_peers=_peers_as_indices(order, deck._surr_peers, slots_list),
        diag_peers=_peers_as_indices(order, deck._diag_peers, slots_list),
        arcane_slot_indices=[] if nitwit else [order[p] for p in deck.arcane_slots],
        implicits=imps, cores=[],
        mult_dir_vert=g, mult_dir_horiz=g,
        mult_pure_base=1.0, mult_pure_scale=0.0, mult_equilibrium=0.0, mult_foil=1.0, mult_steadfast=1.0,
        mult_sparkling=1.0, mult_color=1.0, mult_deluxe_flat=float(RW["deluxe_base"]), mult_deluxe_core_base=1.0,
        mult_deluxe_core_scale=0.0, mult_void_core_base=1.0, mult_void_core_scale=0.0, mult_archive_core=1.0,
        greed_additive=True, additive_cores=True, is_shiny="shiny" in cores,
        colors_real=True, complex_cards=False, wv_foil_rules=True,
        rework=rework_arg(cores, deck, order, villager_bonus, deck_color(imps), stage),
    )
    return common, stacks, values, attrs, ident, imps, villager_bonus


def job(deck: Deck, imp_keys: Sequence[str], mystery: bool, cores: Tuple[str, ...], sd: dict, model, stage: str,
        n_iter: int, restarts: int, phase2, seed: int):
    common, stacks, values, attrs, _ident, _imps, _vb = kernel_args(deck, imp_keys, mystery, cores, sd, stage)
    n_attr, terms = model
    return S._ndm_core.run_sa_tagged(
        **common, stacks=stacks, tag_rules=tag_rules(stage), blanket_groups=[], assignable_groups=[],
        min_stat_placed=0, final_pass_nonfoil_evo=False, exact_groups=True, n_iter=n_iter, restarts=restarts,
        auto_place_arcane=True, floor_counts_deluxe=False, seed=seed, schedule=S._live_sa_schedule(), phase2=phase2,
        value_model=(values, attrs, n_attr, terms), defer=True,
    )


def score_raw(deck: Deck, imp_keys: Sequence[str], mystery: bool, cores: Tuple[str, ...], sd: dict, model,
              stage: str, raw) -> float:
    """Score a finished layout (kernel output, stack indices included) under another value model."""
    common, stacks, values, attrs, _ident, _imps, _vb = kernel_args(deck, imp_keys, mystery, cores, sd, stage)
    n_attr, terms = model
    asgn = [(c[0], c[1], c[2], list(c[3])) for c in raw]
    idx = [c[4] if c[4] >= 0 else 0 for c in raw]
    return S._ndm_core.score_tagged(**common, assignment=asgn, value_model=(values, attrs, n_attr, terms),
                                    stack_index=idx)


def pool_for(deck: Deck) -> List[str]:
    return [c for c in POOL if legal((c,), deck)]


def extend(frontier: List[Tuple[str, ...]], deck: Deck, size: int, pool=None) -> List[Tuple[str, ...]]:
    """Every legal core set of `size` that adds one core to a frontier set (deduplicated, sorted)."""
    out = set()
    for base in frontier:
        for c in (pool or pool_for(deck)):
            if c in base:
                continue
            s = tuple(sorted(base + (c,)))
            if len(s) == size and legal(s, deck):
                out.add(s)
    return sorted(out)


def all_sets(deck: Deck, pool=None) -> List[Tuple[str, ...]]:
    pool = pool or pool_for(deck)
    return [s for n in range(1, deck.core_slots + 1) for s in itertools.combinations(pool, n) if legal(s, deck)]
