// Kernel payload for one rework job: port of DeckFAST src/rework.py `kernel_args` / `job`. The legacy NDM multipliers
// are neutralised (the rework path scores through `rework`), colours are real and every card keeps its real tags.

import type { Deck } from "../deck";
import type { ImplicitDef, ImplicitPayload } from "../implicits";
import { buildObjective, type BuiltObjective, type Objective } from "./objective";
import { coreSpec, deckColor, deckImplicits, nitwitValue, tagRules, type RuleSet } from "./rules";
import { stacksFor, type AllowedTypes } from "./stacks";
import type { RuleIn, StackIdent, Stage, StageBundle } from "./types";

export const SCREEN_SCHEDULE = { kind: "target", params: [0.5, 0.001, 0.02, 0.01] };
export const PHASE2 = { top_k: 4, n_iter: 120000, schedule: { kind: "target", params: [0.1, 0.001, 0.005, 0.01] } };

export interface JobSpec {
  deck:         Deck;
  implicitKeys: string[];
  mystery:      boolean;
  cores:        string[];
}

export interface JobContext {
  bundle:     StageBundle;
  catalog:    Record<string, ImplicitDef>;
  rules:      RuleSet;
  objective:  Objective;
  stage:      Stage;
  levelCards: boolean;
  allowed?:   AllowedTypes;
  /** Targeted: the player's tag rules, added to the stage limits. */
  extraRules?: RuleIn[];
  /** Exact: the player's cards, stack key (stackKey) -> count. Only these stacks are offered. */
  exact?:     Map<string, number>;
}

/** Exact-mode key of a stack: kind, card id, card type and colour. */
export function stackKey(id: StackIdent): string {
  return `${id.kind}|${id.id}|${id.t}|${id.color}`;
}

export interface BuiltJob {
  payload:   Record<string, unknown>;
  ident:     StackIdent[];
  imps:      ImplicitPayload[];
  built:     BuiltObjective;
  coreSpec:  [string, number, number, string][];
  nitwit:    number;
}

export function buildJob(spec: JobSpec, ctx: JobContext): BuiltJob {
  const { deck } = spec;
  const { imps, nitwit: villager } = deckImplicits(spec.implicitKeys, spec.mystery, ctx.rules, ctx.catalog);
  const nitwit = villager > 0 || spec.cores.includes("nitwit");
  const all = stacksFor(ctx.bundle, spec.cores, deck, imps, nitwit, ctx.rules,
                       { levelCards: ctx.levelCards, allowed: ctx.allowed,
                         colors: ctx.exact ? ["red", "green", "blue", "yellow"] : undefined });
  let { stacks, ident } = all;
  if (ctx.exact) {
    const keep = ident.map((id, i) => [i, ctx.exact!.get(stackKey(id))] as const).filter(([, n]) => (n ?? 0) > 0);
    stacks = keep.map(([i, n]) => ({ ...all.stacks[i], count: n! }));
    ident = keep.map(([i]) => all.ident[i]);
  }
  const built = buildObjective(ctx.bundle, ctx.objective, ident, ctx.stage);
  const cs = coreSpec(spec.cores, ctx.rules, deckColor(imps));
  const nit = nitwitValue(spec.cores, ctx.rules, villager);
  const g = ctx.rules.greed;
  const payload: Record<string, unknown> = {
    slots: deck.slots.map(([r, c]) => [r, c]),
    row_peers: deck.rowPeers, col_peers: deck.colPeers, surr_peers: deck.surrPeers, diag_peers: deck.diagPeers,
    arcane_slot_indices: nitwit ? [] : deck.arcaneSlotIndices,
    implicits: imps, cores: [],
    mult_dir_vert: g, mult_dir_horiz: g,
    mult_pure_base: 1.0, mult_pure_scale: 0.0, mult_equilibrium: 0.0, mult_foil: 1.0, mult_steadfast: 1.0,
    mult_sparkling: 1.0, mult_color: 1.0, mult_deluxe_flat: ctx.rules.deluxeBase, mult_deluxe_core_base: 1.0,
    mult_deluxe_core_scale: 0.0, mult_void_core_base: 1.0, mult_void_core_scale: 0.0, mult_archive_core: 1.0,
    greed_additive: true, additive_cores: true, is_shiny: spec.cores.includes("shiny"),
    colors_real: true, complex_cards: false, wv_foil_rules: true,
    rework: { cores: cs, nitwit_slots: nit > 0 ? deck.arcaneSlotIndices : [], nitwit_value: nit },
    stacks, tag_rules: [...tagRules(ctx.rules), ...(ctx.extraRules ?? [])],
    blanket_groups: [], assignable_groups: [], min_stat_placed: 0, final_pass_nonfoil_evo: false,
    exact_groups: true, auto_place_arcane: true, floor_counts_deluxe: false,
    schedule: SCREEN_SCHEDULE,
    value_model: built.valueModel,
    n_iter: 0, restarts: 1,
  };
  // Plain data only: deck geometry and bundle curves may arrive as reactive proxies, which workers cannot receive.
  return { payload: JSON.parse(JSON.stringify(payload)), ident, imps, built, coreSpec: cs, nitwit: nit };
}

/** A search request from a built job: budget, restarts, seed, optional phase 2. */
export function withBudget(job: BuiltJob, nIter: number, restarts: number, seed: number,
                           phase2: boolean): Record<string, unknown> {
  return { ...job.payload, n_iter: nIter, restarts, seed, phase2: phase2 ? PHASE2 : null };
}
