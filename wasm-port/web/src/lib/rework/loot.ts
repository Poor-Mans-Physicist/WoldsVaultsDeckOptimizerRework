// Loot objective: omega + 5 x mythic pool hits per 1000 mapped chests (wooden / gilded / living / ornate averaged),
// from the Wold's Vaults greed-rework code (MythicLootScaling, MixinTieredLootTableGenerator, TenosChestRolls). The
// kernel evaluates it exactly (tagsim.rs LootTerm); this module only supplies the constants.
//
// Assumptions (shown in the UI): no greed medallion, crystal / map modifiers, Aspect of Treasure, Speed Demon or Luck
// potions. Tenos Massive Chests is modelled as intended (+20 to the roll cap, player rarity halved), not as the build
// currently ships it (it also adds base rolls). Expert Looter (+4 to the cap) is assumed taken at End and Max;
// Massive Chests is an End / Max option (the better path counts).

import type { Stage } from "./types";

export const CHEST_WEIGHTS: Record<string, [number, number, number, number, number]> = {
  wooden: [70, 20, 8, 2, 0.00445],
  gilded: [70, 20, 4, 2, 0.00445],
  living: [70, 20, 8, 2, 0.00445],
  ornate: [80, 20, 8, 2, 0.00445],
};
export const BASE_ROLLS = [12, 13, 14, 15];
export const MYTHIC_WEIGHT = 5;
export const ROLL_CAP = 54;
export const EXPERT_LOOTER = 4;
export const MASSIVE_CHESTS = { rolls: 20, rarity: 0.5 };

/** Stage baselines from gear and the rest of the build (fractions: 1.0 = +100%). */
export const LOOT_BASE: Record<Stage, { iq: number; ir: number }> = {
  early: { iq: 0.5, ir: 1.0 },
  mid:   { iq: 1.0, ir: 2.0 },
  end:   { iq: 2.0, ir: 5.0 },
  max:   { iq: 4.0, ir: 10.0 },
};

export interface LootPath {
  label:  string;
  cap:    number;
  rarity: number;
}

export function lootPaths(stage: Stage): LootPath[] {
  if (stage === "early" || stage === "mid") return [{ label: "No Tenos chest nodes", cap: ROLL_CAP, rarity: 1 }];
  const cap = ROLL_CAP + EXPERT_LOOTER;
  return [
    { label: "Expert Looter", cap, rarity: 1 },
    { label: "Expert Looter + Massive Chests", cap: cap + MASSIVE_CHESTS.rolls, rarity: MASSIVE_CHESTS.rarity },
  ];
}

/** Kernel LootTerm (tagsim_wasm LootIn) for deck IQ / IR attribute indices. */
export function lootTerm(stage: Stage, iqAttr: number, irAttr: number) {
  const base = LOOT_BASE[stage];
  return {
    iq_attr: iqAttr, ir_attr: irAttr, base_q: base.iq, base_r: base.ir, base_rolls: BASE_ROLLS,
    chests: Object.values(CHEST_WEIGHTS), mythic_weight: MYTHIC_WEIGHT,
    paths: lootPaths(stage).map((p) => [p.cap, p.rarity] as [number, number]), scale: 1000,
  };
}
