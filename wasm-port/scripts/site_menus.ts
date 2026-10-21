// Gate 3 input: the site's own designs for one stage, in the ledger's deck-menu format.
//
// For every design in the ledger's menu (data/deck_menus_rework.json), the site searches the same deck and modifiers
// (Balanced objective, every core owned) and keeps its best core set and card layout. The output is a menu file the
// ledger can load in place of its own (wv-combat-model tools: deck-only search per build), so the gap between the
// site's choices and the ledger's menus is measured in cycles.
//
//   npx esbuild wasm-port/scripts/site_menus.ts --bundle --platform=node --format=esm --outfile=<dir>/sm.mjs
//   node <dir>/sm.mjs <repo>/wasm-port/web/public <wasm-node>/ndm_core.js <ledger menus.json> <stage> <out.json>
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { buildDeck, type RawDeck } from "../web/src/lib/deck";
import type { ImplicitDef } from "../web/src/lib/implicits";
import { playstyleObjectives } from "../web/src/lib/rework/objective";
import { coreSpec, deckColor, deckImplicits, nitwitValue, stageRules } from "../web/src/lib/rework/rules";
import { searchRework, type DepthPreset } from "../web/src/lib/rework/search";
import type { ReworkConfig, Stage, StageBundle } from "../web/src/lib/rework/types";

const [, , pub, wasmPath, menuPath, stage, outPath] = process.argv;
const require = createRequire(import.meta.url);
const wasm = require(wasmPath);
const cfg = JSON.parse(readFileSync(`${pub}/config.json`, "utf8"));
const rw: ReworkConfig = cfg.rework;
const decks = JSON.parse(readFileSync(`${pub}/decks.json`, "utf8"));
const catalog: Record<string, ImplicitDef> = decks._implicits;
const bundle: StageBundle = JSON.parse(readFileSync(`${pub}/rework/${stage}.json`, "utf8"));
const ledger = JSON.parse(readFileSync(menuPath, "utf8"))[stage];
const rules = stageRules(rw, stage as Stage);
const objectives = playstyleObjectives(bundle, false).filter((o) => o.kind === "balanced");
const depth: DepthPreset = { label: "gate", screen: [1, 10000], full: [4, 40000], beam: 4, final: 2, pairs: 0 };
const runner = async (p: Record<string, unknown>) => wasm.runSaTagged(p);

const menu: unknown[] = [];
const log: string[] = [];
const deckmod: number = cfg.modes.wolds.deckmod ?? 0;
const seen = new Set<string>();
const shapes: { m: any; deck: ReturnType<typeof buildDeck> }[] = [];
for (const m of ledger.menu) {
  const sig = `${m.deck}|${m.implicit_keys.join("+")}`;
  if (seen.has(sig)) continue;
  seen.add(sig);
  let raw = (decks.wolds as RawDeck[]).find((d) => d.name === m.deck);
  if (!raw) {
    const x = (bundle.extra_decks ?? []).find((d) => d.ledger_deck === m.deck);
    if (x) raw = { key: x.key, name: x.name, slots: x.slots, arcane_slots: x.arcane_slots,
                   base_core_slots: x.core_slots - deckmod, min_regular: 0, max_greed: 0, implicit: { kind: "mystery" } };
  }
  if (!raw) {
    console.error(`[site_menus] FALLBACK: ${stage} ${m.label}: deck ${m.deck} is not a site deck; skipped`);
    log.push(`skipped ${m.label}`);
    continue;
  }
  shapes.push({ m, deck: buildDeck(raw, deckmod) });
}
for (const objective of objectives) for (const { m, deck } of shapes) {
  const t0 = performance.now();
  const res = await searchRework({
    ctx: { bundle, catalog, rules, objective, stage: stage as Stage, levelCards: true },
    decks: [{ deck, implicitKeys: m.implicit_keys, mystery: m.mystery, freePair: false }],
    owned: rules.pool, depth, seed: 20261020, runner, lanes: 1,
  });
  const best = res.best;
  const { imps, nitwit: villager } = deckImplicits(m.implicit_keys, m.mystery, rules, catalog);
  const cores = best.spec.cores;
  const name = cores.join("+");
  const nit = nitwitValue(cores, rules, villager);
  const cells = best.result.assignment.map((p) => {
    const id = p.stack >= 0 ? best.job.ident[p.stack] : null;
    let seed: unknown = null;
    if (id?.kind === "evo" || id?.kind === "typeless" || id?.kind === "deluxe") seed = { kind: id.kind, id: id.id, color: p.color };
    else if (id?.kind === "knack") seed = { kind: "battery", id: "Knack", color: p.color };
    else if (id?.kind === "battery") seed = { kind: "battery", id: id.id, color: p.color };
    return { t: p.t, color: p.color, groups: p.groups, seed };
  });
  menu.push({
    label: `${m.label} (site, ${objective.group})`, deck: m.deck, key: m.key, found_for: objective.group, mystery: m.mystery,
    implicit_keys: m.implicit_keys, core_slots: deck.core_slots, cores, core_sets: [cores],
    core_spec: { [name]: coreSpec(cores, rules, deckColor(imps)) }, nitwit: { [name]: nit },
    implicits: imps.map((t) => [t.kind, t.value, t.groups, t.colors, t.extra]),
    slots: deck.slots.map(([r, c]) => [r, c]), arcane_slots: deck.arcaneSlots.map(([r, c]) => [r, c]),
    cells, score: best.score, score_by_group: {}, score_by_build: {},
  });
  log.push(`${objective.group} / ${m.label}: site ${best.score.toFixed(3)} [${name}] vs ledger menu [${m.cores.join("+")}] ` +
           `${(performance.now() - t0) / 1000 | 0}s`);
  console.log(log[log.length - 1]);
}
writeFileSync(outPath, JSON.stringify({ [stage]: { ...ledger, menu, ranking: {}, regret: undefined, site_log: log } }));
console.log(`wrote ${menu.length} site designs to ${outPath}`);
