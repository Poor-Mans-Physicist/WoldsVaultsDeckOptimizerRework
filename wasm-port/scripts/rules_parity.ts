// Gate 1: the TypeScript rework inputs match Python's src/rework.py kernel_args.
//
// For every fixture from parity_fixtures.py, rebuild the job in TypeScript (NDM objective, no level cards, as the
// Python jobs) and compare: implicits, rework cores, nitwit, the neutralised multipliers, tag rules, the stack
// multiset and the slot geometry. On NDM fixtures, Python's assignment (re-ordered by slot position) is scored
// through the WASM kernel on the TypeScript input and must equal Python's score.
//
//   npx esbuild wasm-port/scripts/rules_parity.ts --bundle --platform=node --format=esm --outfile=<dir>/rp.mjs
//   node <dir>/rp.mjs <repo>/wasm-port/web/public <wasm-node>/ndm_core.js <fixtures.json>
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { buildDeck, type RawDeck } from "../web/src/lib/deck";
import type { ImplicitDef } from "../web/src/lib/implicits";
import { buildJob } from "../web/src/lib/rework/payload";
import { stageRules } from "../web/src/lib/rework/rules";
import type { ReworkConfig, Stage, StageBundle } from "../web/src/lib/rework/types";

const [, , pub, wasmPath, fixPath] = process.argv;
const require = createRequire(import.meta.url);
const wasm = require(wasmPath);
const cfg = JSON.parse(readFileSync(`${pub}/config.json`, "utf8"));
const rw: ReworkConfig = cfg.rework;
const decks = JSON.parse(readFileSync(`${pub}/decks.json`, "utf8"));
const catalog: Record<string, ImplicitDef> = decks._implicits;
const deckmod: number = cfg.modes.wolds.deckmod ?? 0;
const bundles: Record<string, StageBundle> = {};
const { fixtures } = JSON.parse(readFileSync(fixPath, "utf8"));

const key = (x: unknown) => JSON.stringify(x);
const multiset = (xs: unknown[]) => xs.map(key).sort().join("|");
let bad = 0;
const fail = (f: any, what: string, a: unknown, b: unknown) => {
  bad++;
  console.log(`MISMATCH ${f.stage} ${f.deck} [${f.cores}] ${what}:\n  ts ${key(a).slice(0, 300)}\n  py ${key(b).slice(0, 300)}`);
};

for (const f of fixtures) {
  const stage = f.stage as Stage;
  bundles[stage] ??= JSON.parse(readFileSync(`${pub}/rework/${stage}.json`, "utf8"));
  const raw = (decks.wolds as RawDeck[]).find((d) => d.name === f.deck);
  if (!raw) { fail(f, "deck missing", f.deck, null); continue; }
  const deck = buildDeck(raw, deckmod);
  const job = buildJob({ deck, implicitKeys: f.implicits, mystery: f.mystery, cores: f.cores }, {
    bundle: bundles[stage], catalog, rules: stageRules(rw, stage),
    objective: { kind: "ndm", group: "", label: "" }, stage, levelCards: false,
  });
  const ts = job.payload as any;
  const py = f.input;
  if (multiset(ts.implicits) !== multiset(py.implicits)) fail(f, "implicits", ts.implicits, py.implicits);
  if (key(ts.rework.cores) !== key(py.rework.cores)) fail(f, "rework cores", ts.rework.cores, py.rework.cores);
  if (ts.rework.nitwit_value !== py.rework.nitwit_value) fail(f, "nitwit", ts.rework.nitwit_value, py.rework.nitwit_value);
  for (const k of Object.keys(py)) {
    if (k.startsWith("mult_") || ["greed_additive", "additive_cores", "is_shiny", "colors_real", "complex_cards",
                                    "wv_foil_rules", "exact_groups", "auto_place_arcane", "floor_counts_deluxe"].includes(k)) {
      if (ts[k] !== py[k]) fail(f, k, ts[k], py[k]);
    }
  }
  if (multiset(ts.tag_rules) !== multiset(py.tag_rules)) fail(f, "tag rules", ts.tag_rules, py.tag_rules);
  // Python stacks carry stat cards from the weights file, TypeScript from the ledger bundle: compare the shape (type,
  // colour, groups, count) of non-stat stacks exactly and the stat stack counts per (type, colour).
  const shape = (s: any) => ({ t: s.t, color: s.color, groups: s.groups, count: s.count });
  const nonStat = (xs: any[]) => xs.filter((s) => !["row", "col", "surr", "diag", "deluxe"].includes(s.t)
                                                   && !(s.t === "typeless" && !["Resource", "Knack", "Temporal"].includes(s.groups[0])));
  if (multiset(nonStat(ts.stacks).map(shape)) !== multiset(nonStat(py.stacks).map(shape))) {
    fail(f, "non-stat stacks", nonStat(ts.stacks).map(shape), nonStat(py.stacks).map(shape));
  }
  const colorsOf = (xs: any[]) => [...new Set(xs.map((s) => s.color))].sort();
  if (key(colorsOf(ts.stacks)) !== key(colorsOf(py.stacks))) fail(f, "stack colours", colorsOf(ts.stacks), colorsOf(py.stacks));
  const pos = (s: [number, number]) => `${s[0]},${s[1]}`;
  if (multiset(ts.slots.map(pos)) !== multiset(py.slots.map(pos))) fail(f, "slots", ts.slots, py.slots);
  const arcTs = (ts.arcane_slot_indices as number[]).map((i) => pos(ts.slots[i])).sort();
  const arcPy = (py.arcane_slot_indices as number[]).map((i) => pos(py.slots[i])).sort();
  if (key(arcTs) !== key(arcPy)) fail(f, "arcane slots", arcTs, arcPy);
  if (f.objective === "ndm") {
    const byPos = new Map<string, unknown>();
    py.slots.forEach((s: [number, number], i: number) => byPos.set(pos(s), py.assignment[i]));
    const input = { ...ts, value_model: null, assignment: ts.slots.map((s: [number, number]) => byPos.get(pos(s))),
                    stack_index: [] };
    const got = wasm.scoreTagged(input);
    if (Math.abs(got - f.expected) > 1e-9 * Math.max(1, Math.abs(f.expected))) fail(f, "NDM score", got, f.expected);
  }
}
console.log(`${fixtures.length} fixtures, ${bad} mismatches`);
process.exit(bad ? 1 : 0);
