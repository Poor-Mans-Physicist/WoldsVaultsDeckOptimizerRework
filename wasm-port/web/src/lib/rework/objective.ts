// Objectives: the playstyle picker's choices as kernel value models.
//
// A build-group objective averages the group's ledger builds; each build scores min(damage cycles, survival cycles)
// with every stat curve applied to the deck's totals and every level curve to the deck's granted levels (curves are
// probed around the build's own deck, so its base cycles are the channel bases). Offensive / Defensive keep one
// channel. Loot is the kernel's exact loot term; classic NDM uses no value model.

import { lootTerm } from "./loot";
import type { StackIdent, Stage, StageBundle } from "./types";

export type ObjectiveKind = "balanced" | "offense" | "defense" | "loot" | "ndm";

export interface Objective {
  kind:  ObjectiveKind;
  /** Build group for balanced: a key of the bundle's playstyles. */
  group: string;
  label: string;
}

export const IQ_ATTR = "the_vault:item_quantity";
export const IR_ATTR = "the_vault:item_rarity";

/** The playstyle picker's entries for a stage bundle, Balanced first. */
export function playstyleObjectives(bundle: StageBundle, withNdm: boolean): Objective[] {
  const out: Objective[] = [{ kind: "balanced", group: "Balanced", label: "Balanced (all builds)" }];
  for (const g of Object.keys(bundle.playstyles)) {
    if (g !== "Balanced" && bundle.playstyles[g].length > 0) out.push({ kind: "balanced", group: g, label: g });
  }
  out.push({ kind: "offense", group: "Balanced", label: "Offensive (damage only)" });
  out.push({ kind: "defense", group: "Balanced", label: "Defensive (survival only)" });
  out.push({ kind: "loot", group: "", label: "Loot (omega and mythic rolls)" });
  if (withNdm) out.push({ kind: "ndm", group: "", label: "Classic NDM" });
  return out;
}

export interface ValueModelIn {
  stack_value:    number[];
  stack_attr:     number[];
  stack_unscaled: boolean[];
  n_attr:         number;
  terms:          unknown[];
}

export interface BuiltObjective {
  valueModel: ValueModelIn | null;
  /** Attribute names by index: stat attributes, then "talent:<key>" / "ability:<key>" level keys. */
  attrNames:  string[];
  members:    string[];
}

const levelName = (kind: string, key: string) => `${kind}:${key}`;

export function buildObjective(bundle: StageBundle, obj: Objective, ident: readonly StackIdent[],
                               stage: Stage): BuiltObjective {
  if (obj.kind === "ndm") return { valueModel: null, attrNames: [], members: [] };
  const members = obj.kind === "loot" ? [] : (bundle.playstyles[obj.group] ?? []);
  if (obj.kind !== "loot" && members.length === 0) throw new Error(`rework: playstyle ${obj.group} has no builds`);
  const builds = bundle.builds.filter((b) => members.includes(b.family));
  const attrNames = [...bundle.attrs];
  const index = new Map(attrNames.map((a, i) => [a, i]));
  const usedLevels = new Set<string>();
  for (const b of builds) for (const [key, lc] of Object.entries(b.levels)) usedLevels.add(levelName(lc.kind, key));
  for (const id of ident) {
    if (!id.grant) continue;
    const name = levelName(id.grant[0], id.grant[1]);
    if (usedLevels.has(name) && !index.has(name)) { index.set(name, attrNames.length); attrNames.push(name); }
  }
  if (attrNames.length > 128) throw new Error(`rework: ${attrNames.length} value attributes, kernel max 128`);
  const stack_value: number[] = [], stack_attr: number[] = [], stack_unscaled: boolean[] = [];
  for (const id of ident) {
    if (id.grant) {
      const a = index.get(levelName(id.grant[0], id.grant[1]));
      stack_attr.push(a ?? -1); stack_value.push(id.grant[2]); stack_unscaled.push(true);
    } else if (id.attr) {
      const a = index.get(id.attr);
      stack_attr.push(a ?? -1); stack_value.push(id.value ?? 0); stack_unscaled.push(false);
    } else {
      stack_attr.push(-1); stack_value.push(0); stack_unscaled.push(false);
    }
  }
  let terms: unknown[];
  if (obj.kind === "loot") {
    const iq = index.get(IQ_ATTR), ir = index.get(IR_ATTR);
    if (iq === undefined || ir === undefined) throw new Error("rework: the bundle has no Item Quantity / Rarity cards");
    terms = [{ weight: 1, channels: [], extra: [], loot: lootTerm(stage, iq, ir) }];
  } else {
    const curves = (b: (typeof builds)[number], ch: "d" | "s") => {
      const out: [number, { x0: number; dx: number; ys: number[] }][] = [];
      for (const [a, c] of Object.entries(b.curves)) {
        const i = index.get(a);
        if (i !== undefined) out.push([i, { x0: c.x0, dx: c.dx, ys: c[ch] }]);
      }
      for (const [key, lc] of Object.entries(b.levels)) {
        const i = index.get(levelName(lc.kind, key));
        if (i !== undefined) out.push([i, { x0: 0, dx: 1, ys: lc[ch] }]);
      }
      return out;
    };
    const w = 1 / builds.length;
    terms = builds.map((b) => {
      const dmg = { base: b.damage, curves: curves(b, "d") };
      const srv = { base: b.survival, curves: curves(b, "s") };
      const channels = obj.kind === "offense" ? [dmg] : obj.kind === "defense" ? [srv] : [dmg, srv];
      return { weight: w, channels, extra: [] };
    });
  }
  return {
    valueModel: { stack_value, stack_attr, stack_unscaled, n_attr: attrNames.length, terms },
    attrNames, members,
  };
}
