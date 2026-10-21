// Time rework jobs through the WASM kernel (Node build) and show what a run places.
//   npx esbuild wasm-port/scripts/bench_rework.ts --bundle --platform=node --format=esm --outfile=<dir>/bench.mjs
//   node <dir>/bench.mjs <repo>/wasm-port/web/public <wasm-node>/ndm_core.js <stage> <deck name> <cores,..> <objective>
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { buildDeck, type RawDeck } from "../web/src/lib/deck";
import type { ImplicitDef } from "../web/src/lib/implicits";
import { playstyleObjectives } from "../web/src/lib/rework/objective";
import { buildJob, withBudget } from "../web/src/lib/rework/payload";
import { stageRules } from "../web/src/lib/rework/rules";
import type { ReworkConfig, Stage, StageBundle } from "../web/src/lib/rework/types";

const [, , pub, wasmPath, stage, deckName, coreList, objLabel] = process.argv;
const require = createRequire(import.meta.url);
const wasm = require(wasmPath);
const cfg = JSON.parse(readFileSync(`${pub}/config.json`, "utf8"));
const decks = JSON.parse(readFileSync(`${pub}/decks.json`, "utf8"));
const catalog: Record<string, ImplicitDef> = decks._implicits;
const bundle: StageBundle = JSON.parse(readFileSync(`${pub}/rework/${stage}.json`, "utf8"));
const raw = (decks.wolds as RawDeck[]).find((d) => d.name === deckName)!;
const deck = buildDeck(raw, cfg.modes.wolds.deckmod ?? 0);
const objective = playstyleObjectives(bundle, true).find((o) => o.label.startsWith(objLabel))!;
const keys = raw.implicit && raw.implicit.kind !== "mystery" ? [raw.key] : [];
const job = buildJob({ deck, implicitKeys: keys, mystery: false, cores: coreList ? coreList.split(",") : [] }, {
  bundle, catalog, rules: stageRules(cfg.rework as ReworkConfig, stage as Stage), objective, stage: stage as Stage,
  levelCards: true,
});
console.log(`${deckName} ${stage} [${coreList}] ${objective.label}: ${job.ident.length} stacks, ` +
            `${job.built.attrNames.length} attributes, ${(job.payload.value_model as any)?.terms.length ?? 0} terms`);
for (const [n, r] of [[20000, 2], [40000, 4]] as const) {
  const t0 = performance.now();
  const res = wasm.runSaTagged(withBudget(job, n, r, 7, false));
  console.log(`  ${r} x ${n}: score ${res.score.toFixed(4)} in ${((performance.now() - t0) / 1000).toFixed(2)} s`);
  if (n === 40000) {
    const kinds: Record<string, number> = {};
    for (const c of res.assignment) {
      const id = c.stack >= 0 ? job.ident[c.stack] : null;
      const k = id ? (id.grant ? `${id.kind}:${id.grant[1]}` : id.attr ? `${id.kind}:${id.attr.split(":")[1]}` : id.kind) : "dead";
      kinds[k] = (kinds[k] ?? 0) + 1;
    }
    console.log("  placed:", JSON.stringify(kinds));
  }
}
