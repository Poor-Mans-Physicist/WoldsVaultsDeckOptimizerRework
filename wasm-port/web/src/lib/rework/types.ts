// Types for the deck/core rework: the stage rules (config.json `rework`, from DeckFAST config.yaml) and the ledger
// data bundle (public/rework/<stage>.json, written by wv-combat-model tools/site_bundle.py).

export type Stage = "early" | "mid" | "end" | "max";
export const STAGES: readonly Stage[] = ["early", "mid", "end", "max"];
export const STAGE_LABEL: Record<Stage, string> = { early: "Early", mid: "Mid", end: "End", max: "Max" };

export type RollTier = "normal_mid" | "normal_top" | "greater_top";

export interface RollRange {
  normal:  [number, number];
  greater: [number, number];
}

export interface StageRule {
  greed:   number;
  rolls:   RollTier;
  mystery: boolean;
  limits:  Record<string, number>;
}

export interface ReworkConfig {
  mystery_scale:   number;
  runic_tag:       number;
  shadow_void:     number;
  villager_nitwit: number;
  deluxe_base:     number;
  rolls:           Record<string, RollRange>;
  empyreal_slots:  { normal: number; greater: number };
  pool:            string[];
  stages:          Record<Stage, StageRule>;
}

export interface StatCard {
  id:     string;
  attr:   string;
  value:  number;
  groups: string[];
}

export type GrantKind = "ability" | "talent";

export interface SpecialCard {
  id:     string;
  groups: string[];
  grant:  [GrantKind, string, number] | null;
}

export interface CurveData {
  x0: number;
  dx: number;
  d:  number[];
  s:  number[];
}

export interface LevelCurve {
  kind: GrantKind;
  l0:   number;
  d:    number[];
  s:    number[];
}

export interface BuildData {
  family:   string;
  label:    string;
  scaling:  string;
  score:    number;
  damage:   number;
  survival: number;
  x0:       Record<string, number>;
  curves:   Record<string, CurveData>;
  levels:   Record<string, LevelCurve>;
}

export interface BundleMeta {
  ledger_commit:     string;
  results_generated: string;
  bundle_generated:  string;
}

export interface StageBundle {
  meta:       BundleMeta;
  stage:      Stage;
  card_tier:  number;
  attrs:      string[];
  xmax:       Record<string, number>;
  cards:      { evo: StatCard[]; typeless: StatCard[]; deluxe: StatCard[] };
  special:    { knack: SpecialCard[]; temporal: SpecialCard[]; resource: SpecialCard[]; arcane: SpecialCard[] };
  level_keys: { kind: GrantKind; key: string }[];
  level_max:  number;
  playstyles: Record<string, string[]>;
  builds:     BuildData[];
}

/** One kernel stack's identity, parallel to the payload's `stacks`. */
export interface StackIdent {
  kind:   "evo" | "typeless" | "deluxe" | "battery" | "greed" | "arcane" | "knack";
  id:     string;
  color:  string;
  t:      string;
  /** Stat cards: the attribute and value. */
  attr?:  string;
  value?: number;
  /** Level cards: the grant. */
  grant?: [GrantKind, string, number];
}

/** Kernel stack (tagsim_wasm StackIn). */
export interface StackIn {
  t:           string;
  color:       string;
  scale_color: string;
  groups:      string[];
  count:       number | null;
  min_place:   number;
}

/** Kernel tag rule (tagsim_wasm RuleIn). */
export interface RuleIn {
  axis: "color" | "type" | "group" | "greed";
  key:  string;
  min:  number;
  max:  number | null;
}
