// Rework stage rules: TypeScript port of DeckFAST src/rework.py (roll, greed, empyreal slots, Mystery scaling,
// implicits, legality, core specs, card limits). A RuleSet is either a game stage's rules (Global mode) or the
// player's typed values (Targeted / Exact).

import type { ImplicitDef, ImplicitPayload } from "../implicits";
import { toPayload } from "../implicits";
import type { Deck } from "../deck";
import type { ReworkConfig, RuleIn, Stage } from "./types";

export const COLORS = ["red", "green", "blue", "yellow"] as const;
export const BATTERY: Record<string, string> = { bazaar: "Resource", talent: "Knack", temporal: "Temporal" };
const NO_EFFECT = new Set(["gameplay", "mystery"]);

export interface RuleSet {
  /** Greed card bonus (+0.75 = +75%). */
  greed:          number;
  /** The value each core uses (multiplicative cores as the bonus: x1.5 -> 0.5). */
  coreValue:      (core: string) => number;
  empyrealSlots:  number;
  mysteryScale:   number;
  deluxeBase:     number;
  runicTag:       number;
  shadowVoid:     number;
  villagerNitwit: number;
  /** Separate caps: knack / temporal / resource / deluxe. */
  limits:         Record<string, number>;
  mysteryAllowed: boolean;
  pool:           string[];
}

export function roll(cfg: ReworkConfig, core: string, stage: Stage): number {
  const tier = cfg.stages[stage].rolls;
  const r = cfg.rolls[core];
  if (!r) throw new Error(`rework: no roll range for core ${core}`);
  if (tier === "normal_mid") return 0.5 * (r.normal[0] + r.normal[1]);
  if (tier === "normal_top") return r.normal[1];
  if (tier === "greater_top") return r.greater[1];
  throw new Error(`rework: unknown roll tier ${tier} for stage ${stage}`);
}

export function stageRules(cfg: ReworkConfig, stage: Stage): RuleSet {
  const st = cfg.stages[stage];
  return {
    greed:          st.greed,
    coreValue:      (core) => roll(cfg, core, stage),
    empyrealSlots:  cfg.empyreal_slots[st.rolls.startsWith("greater") ? "greater" : "normal"],
    mysteryScale:   cfg.mystery_scale,
    deluxeBase:     cfg.deluxe_base,
    runicTag:       cfg.runic_tag,
    shadowVoid:     cfg.shadow_void,
    villagerNitwit: cfg.villager_nitwit,
    limits:         { ...(st.limits ?? {}) },
    mysteryAllowed: st.mystery,
    pool:           [...cfg.pool],
  };
}

/** Targeted / Exact: the player's own core values and greed value; no stage limits (tag rules replace them). */
export function manualRules(
  cfg: ReworkConfig, coreValues: Record<string, number>, greed: number, empyrealSlots: number,
): RuleSet {
  return {
    greed,
    coreValue:      (core) => {
      const v = coreValues[core];
      if (v === undefined) throw new Error(`rework: no value entered for core ${core}`);
      return v;
    },
    empyrealSlots,
    mysteryScale:   cfg.mystery_scale,
    deluxeBase:     cfg.deluxe_base,
    runicTag:       cfg.runic_tag,
    shadowVoid:     cfg.shadow_void,
    villagerNitwit: cfg.villager_nitwit,
    limits:         {},
    mysteryAllowed: true,
    pool:           Object.keys(coreValues),
  };
}

/** Modifier keys a Mystery deck can roll that change card values (gameplay-only ones are skipped). */
export function mysteryPool(catalog: Record<string, ImplicitDef>): string[] {
  return Object.keys(catalog).sort().filter((key) => {
    const kind = catalog[key]?.kind;
    return !(NO_EFFECT.has(kind) && key !== "villager");
  });
}

/** Kernel implicits for these implicit keys plus the Villager Nitwit bonus. Mystery decks inherit each modifier at
 *  the Mystery scale: frequency multipliers as 1 + s(v - 1), every other value as v x s. */
export function deckImplicits(
  keys: readonly string[], mystery: boolean, rules: RuleSet, catalog: Record<string, ImplicitDef>,
): { imps: ImplicitPayload[]; nitwit: number } {
  const s = mystery ? rules.mysteryScale : 1.0;
  const imps: ImplicitPayload[] = [];
  let nitwit = 0;
  for (const key of keys) {
    if (key === "villager") { nitwit += rules.villagerNitwit * s; continue; }
    if (key === "shadow") { imps.push({ kind: "empty_slots", value: rules.shadowVoid * s, groups: [], colors: [], extra: "" }); continue; }
    if (key === "runic") { imps.push({ kind: "mirror", value: rules.runicTag * s, groups: [], colors: [], extra: "" }); continue; }
    const def = catalog[key];
    if (!def) {
      console.error(`[rework] FALLBACK: implicit ${key} is not in the catalog; ignored`);
      continue;
    }
    if (NO_EFFECT.has(def.kind)) continue;
    const p = toPayload(def);
    p.value = p.kind === "freq" ? 1.0 + s * (p.value - 1.0) : p.value * s;
    imps.push(p);
  }
  return { imps, nitwit };
}

export function deckColor(imps: readonly ImplicitPayload[]): string {
  for (const imp of imps) if (imp.colors.length) return imp.colors[0];
  return "red";
}

export function legal(cores: readonly string[], deck: Deck): boolean {
  const has = (c: string) => cores.includes(c);
  if ((has("archive") || has("nitwit")) && deck.arcaneSlots.length === 0) return false;
  return !(has("archive") && has("nitwit"));
}

export function poolFor(deck: Deck, pool: readonly string[]): string[] {
  return pool.filter((c) => legal([c], deck));
}

/** Every legal core set of `size` that adds one core to a frontier set (deduplicated, sorted). */
export function extend(frontier: readonly string[][], deck: Deck, size: number, pool: readonly string[]): string[][] {
  const out = new Map<string, string[]>();
  for (const base of frontier) {
    for (const c of pool) {
      if (base.includes(c)) continue;
      const s = [...base, c].sort();
      if (s.length === size && legal(s, deck)) out.set(s.join("+"), s);
    }
  }
  return [...out.keys()].sort().map((k) => out.get(k)!);
}

/** Resolved core list (name, value, k, color) for the kernel; Nitwit is not a kernel core. */
export function coreSpec(cores: readonly string[], rules: RuleSet, color: string): [string, number, number, string][] {
  return cores.filter((c) => c !== "nitwit").map((c) => [
    c, rules.coreValue(c), c === "empyreal" ? rules.empyrealSlots : 0, c === "color" ? color : "",
  ]);
}

export function nitwitValue(cores: readonly string[], rules: RuleSet, villagerBonus: number): number {
  return villagerBonus + (cores.includes("nitwit") ? rules.coreValue("nitwit") : 0);
}

/** Card limits as kernel tag rules (each limit is its own cap). */
export function tagRules(rules: RuleSet): RuleIn[] {
  const out: RuleIn[] = [];
  if ("deluxe" in rules.limits) out.push({ axis: "type", key: "deluxe", min: 0, max: rules.limits.deluxe });
  for (const g of ["knack", "temporal", "resource"]) {
    if (g in rules.limits) out.push({ axis: "group", key: g[0].toUpperCase() + g.slice(1), min: 0, max: rules.limits[g] });
  }
  return out;
}
