// Kernel stacks for one rework job: port of DeckFAST src/rework.py `stacks_for`, plus level cards.
//
// Every stat card at the stage tier is its own stack with its real tags (tag-gated cores only reach cards carrying
// the tag), in every colour when a rule rewards several colours (Equilibrium, Puzzle) and in the deck's colour
// otherwise. Battery cards (Resource / Knack / Temporal, worth nothing themselves) are offered when a core or an
// adjacency implicit reads them. With `levelCards`, every Knack card that grants a combat talent is its own stack,
// and the Arcane slots choose among the pooled Arcane cards instead of one generic Arcane card.

import type { Deck } from "../deck";
import type { ImplicitPayload } from "../implicits";
import { BATTERY, COLORS, deckColor, type RuleSet } from "./rules";
import type { StackIdent, StackIn, StageBundle, StatCard } from "./types";

export const KERNEL_GROUPS = new Set([
  "Offensive", "Defensive", "Physical", "Magical", "Utility", "Resource", "Knack", "Temporal", "Essence", "Foil", "Stat",
]);
export const POSITIONAL = ["row", "col", "surr", "diag"] as const;
export const GREEDS = ["dir_greed_up", "dir_greed_down", "dir_greed_left", "dir_greed_right"] as const;

/** Card types the player allows (Targeted); undefined allows everything. */
export type AllowedTypes = Partial<Record<"evo" | "typeless" | "deluxe" | "greed" | "arcane" | "knack" | "battery", boolean>>;

export interface StackOptions {
  levelCards: boolean;
  allowed?:   AllowedTypes;
  /** Colours to offer stat cards in (Exact: every colour); default by the deck's rules. */
  colors?:    string[];
}

export interface StackSet {
  stacks: StackIn[];
  ident:  StackIdent[];
}

function kernelGroups(groups: readonly string[]): string[] {
  return groups.filter((g) => KERNEL_GROUPS.has(g) && g !== "Stat").sort();
}

export function stacksFor(
  bundle: StageBundle, cores: readonly string[], deck: Deck, imps: readonly ImplicitPayload[], nitwit: boolean,
  rules: RuleSet, opts: StackOptions,
): StackSet {
  const shiny = cores.includes("shiny");
  const multi = cores.includes("equilibrium") || imps.some((t) => t.kind === "color_mismatch");
  const colors: string[] = opts.colors ?? (multi ? [...COLORS] : [deckColor(imps)]);
  const ok = (k: keyof AllowedTypes) => opts.allowed?.[k] !== false;
  const stacks: StackIn[] = [];
  const ident: StackIdent[] = [];

  const addStat = (t: string, kind: "evo" | "typeless" | "deluxe", card: StatCard, color: string) => {
    const groups = kernelGroups(card.groups);
    if (shiny) groups.push("Foil");
    stacks.push({ t, color, scale_color: "", groups, count: null, min_place: 0 });
    ident.push({ kind, id: card.id, color, t, attr: card.attr, value: card.value });
  };

  for (const color of colors) {
    if (ok("evo")) for (const t of POSITIONAL) for (const card of bundle.cards.evo) addStat(t, "evo", card, color);
    if (ok("typeless")) for (const card of bundle.cards.typeless) addStat("typeless", "typeless", card, color);
    if (ok("deluxe")) for (const card of bundle.cards.deluxe) addStat("deluxe", "deluxe", card, color);
  }
  const batteries = new Set<string>();
  for (const c of cores) if (BATTERY[c]) batteries.add(BATTERY[c]);
  for (const t of imps) if (t.kind === "adjacency") for (const g of t.groups) batteries.add(g);
  if (ok("battery")) {
    for (const g of [...batteries].sort()) {
      const cap = rules.limits[g.toLowerCase()];
      stacks.push({ t: "typeless", color: colors[0], scale_color: "", groups: [g], count: cap ?? null, min_place: 0 });
      ident.push({ kind: "battery", id: g, color: colors[0], t: "typeless" });
    }
  }
  if (ok("greed")) {
    for (const g of GREEDS) {
      stacks.push({ t: g, color: colors[0], scale_color: "", groups: [], count: null, min_place: 0 });
      ident.push({ kind: "greed", id: g, color: colors[0], t: g });
    }
  }
  if (opts.levelCards && ok("knack")) {
    const cap = rules.limits.knack;
    for (const card of bundle.special.knack) {
      if (!card.grant) continue;
      stacks.push({ t: "typeless", color: colors[0], scale_color: "", groups: kernelGroups(card.groups), count: cap ?? null,
                    min_place: 0 });
      ident.push({ kind: "knack", id: card.id, color: colors[0], t: "typeless", grant: card.grant });
    }
  }
  if (deck.arcaneSlots.length > 0 && !nitwit && ok("arcane")) {
    if (opts.levelCards) {
      for (const card of bundle.special.arcane) {
        if (!card.grant) continue;
        stacks.push({ t: "arcane", color: colors[0], scale_color: "", groups: [], count: null, min_place: 0 });
        ident.push({ kind: "arcane", id: card.id, color: colors[0], t: "arcane", grant: card.grant });
      }
    } else {
      stacks.push({ t: "arcane", color: colors[0], scale_color: "", groups: [], count: null, min_place: 0 });
      ident.push({ kind: "arcane", id: "arcane", color: colors[0], t: "arcane" });
    }
  }
  return { stacks, ident };
}
