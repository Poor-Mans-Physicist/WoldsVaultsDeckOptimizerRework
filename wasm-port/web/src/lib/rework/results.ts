// What a finished rework run shows: per slot the card, its stat after multipliers and its breakdown; the deck's stat
// totals and granted levels; and the objective's readouts (cycles for build groups, chest loot for Loot).

import { explainTaggedPayload, lootEvalPayload, type ExplainSlot } from "../optimize";
import { lootPaths, lootTerm, LOOT_BASE } from "./loot";
import { IQ_ATTR, IR_ATTR, type Objective } from "./objective";
import type { Candidate } from "./search";
import type { StackIdent, Stage } from "./types";

export const STAT_LABEL: Record<string, string> = {
  "the_vault:ability_power_percent": "Ability Power",
  "the_vault:area_of_effect": "Area of Effect",
  "the_vault:armor_percentile": "Armor",
  "the_vault:attack_speed_percent": "Attack Speed",
  "the_vault:cooldown_reduction": "Cooldown Reduction",
  "the_vault:critical_hit_mitigation": "Crit Mitigation",
  "the_vault:damage_increase": "Damage",
  "the_vault:healing_effectiveness": "Healing Efficiency",
  "the_vault:health_percentile": "Health",
  "the_vault:item_quantity": "Item Quantity",
  "the_vault:item_rarity": "Item Rarity",
  "the_vault:knockback_resistance": "Knockback Resistance",
  "the_vault:mana_additive": "Mana",
  "the_vault:mana_additive_percentile": "Mana %",
  "the_vault:mana_regen": "Mana Regen",
  "the_vault:resistance": "Resistance",
  "the_vault:thorns_damage_flat_percentile": "Thorns",
};

/** Flat (non-percent) attributes. */
const FLAT = new Set(["the_vault:mana_additive"]);

export function statLabel(attr: string): string {
  return STAT_LABEL[attr] ?? attr.replace(/^the_vault:/, "").replace(/_/g, " ");
}

export function formatStat(attr: string, v: number): string {
  if (FLAT.has(attr)) return `+${Math.round(v).toLocaleString("en-US")}`;
  const pct = v * 100;
  const digits = Math.abs(pct) >= 100 ? 0 : Math.abs(pct) >= 10 ? 1 : 2;
  return `+${pct.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 })}%`;
}

const SRC_LABEL: Record<number, string> = {
  0: "Pure", 1: "Equilibrium", 2: "Steadfast", 3: "Shiny", 4: "Colour", 5: "Premium", 6: "Void", 7: "Archive",
  8: "Empyreal", 9: "Arsenal", 10: "Aegis", 11: "Tool", 12: "Natural", 13: "Fae", 14: "Jupiter", 15: "Pluto",
  16: "Bazaar", 17: "Talent", 18: "Temporal", 19: "Greed core",
  100: "Empty slots", 101: "Nitwit", 110: "Deck modifier", 111: "Deck modifier (adjacent)",
  112: "Deck modifier (colour mismatch)", 113: "Deck modifier (row)", 114: "Deck modifier (chain)",
  115: "Deck modifier (unique tags)", 116: "Runic (mirror)",
};

export function sourceLabel(code: number): string {
  return SRC_LABEL[code] ?? `source ${code}`;
}

export interface SlotView {
  index:   number;
  row:     number;
  col:     number;
  t:       string;
  color:   string;
  arcane:  boolean;
  ident:   StackIdent | null;
  term:    number;
  /** Stat cards: the stat this card adds after deck multipliers. */
  contrib: number | null;
  parts:   ExplainSlot | null;
}

export interface TotalRow {
  attr:  string;
  label: string;
  value: number;
  cards: number;
}

export interface LevelRow {
  kind:   string;
  key:    string;
  levels: number;
  cards:  number;
}

export interface LootView {
  score:   number;
  path:    string;
  iq:      number;
  ir:      number;
  paths:   { label: string; score: number }[];
}

export interface ResultView {
  deckName:  string;
  implicits: string[];
  cores:     [string, number, number, string][];
  score:     number;
  objective: Objective;
  slots:     SlotView[];
  totals:    TotalRow[];
  levels:    LevelRow[];
  loot:      LootView | null;
}

export async function analyse(c: Candidate, objective: Objective, stage: Stage): Promise<ResultView> {
  const { job, result } = c;
  const deck = c.spec.deck;
  const asgn = result.assignment;
  const payload = {
    ...job.payload,
    assignment: asgn.map((p) => [p.t, p.color, p.scale_color, p.groups]),
    stack_index: asgn.map((p) => p.stack),
  };
  const ex = await explainTaggedPayload(payload);
  const arcane = new Set(deck.arcaneSlotIndices);
  const slots: SlotView[] = asgn.map((p, i) => {
    const ident = p.stack >= 0 ? job.ident[p.stack] : null;
    const term = ex.slots[i]?.term ?? 0;
    return {
      index: i, row: deck.slots[i][0], col: deck.slots[i][1], t: p.t, color: p.color, arcane: arcane.has(i), ident, term,
      contrib: ident?.attr && term > 0 ? term * (ident.value ?? 0) : null,
      parts: ex.slots[i] && ex.slots[i].base > 0 ? ex.slots[i] : null,
    };
  });
  const tot = new Map<string, TotalRow>();
  const lev = new Map<string, LevelRow>();
  for (const s of slots) {
    if (s.contrib !== null && s.ident?.attr) {
      const r = tot.get(s.ident.attr) ?? { attr: s.ident.attr, label: statLabel(s.ident.attr), value: 0, cards: 0 };
      r.value += s.contrib; r.cards++;
      tot.set(s.ident.attr, r);
    }
    if (s.ident?.grant && s.t !== "dead") {
      const [kind, key, lv] = s.ident.grant;
      const r = lev.get(`${kind}:${key}`) ?? { kind, key, levels: 0, cards: 0 };
      r.levels += lv; r.cards++;
      lev.set(`${kind}:${key}`, r);
    }
  }
  let loot: LootView | null = null;
  const iq = tot.get(IQ_ATTR)?.value ?? 0, ir = tot.get(IR_ATTR)?.value ?? 0;
  if (objective.kind === "loot" || iq > 0 || ir > 0) {
    const paths = lootPaths(stage);
    const per = await Promise.all(paths.map((_p, i) => lootEvalPayload({
      loot: { ...lootTerm(stage, 0, 1), paths: [[paths[i].cap, paths[i].rarity]] }, iq, ir,
    })));
    const bestI = per.reduce((bi, r, i) => (r.score > per[bi].score ? i : bi), 0);
    loot = { score: per[bestI].score, path: paths[bestI].label, iq: LOOT_BASE[stage].iq + iq,
             ir: LOOT_BASE[stage].ir + ir, paths: paths.map((p, i) => ({ label: p.label, score: per[i].score })) };
  }
  return {
    deckName: deck.name, implicits: c.spec.implicitKeys, cores: job.coreSpec, score: result.score, objective, slots,
    totals: [...tot.values()].sort((a, b) => a.label.localeCompare(b.label)),
    levels: [...lev.values()].sort((a, b) => a.key.localeCompare(b.key)),
    loot,
  };
}
