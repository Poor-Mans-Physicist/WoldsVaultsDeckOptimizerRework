// Full deck pass for the Cores tab: every deck, every playstyle, every stage, on the site's own engine and the ledger
// data bundle. For each (stage, playstyle, deck) a beam over subsets of every core finds the best core set (screen
// budget), and the winner is re-run in full. From the screened sets come each core's pick count (decks whose best set
// holds it) and its gain (best set with the core minus best set without it, averaged over the decks it is legal on).
// Mystery modifiers count as decks of their own (the plain Mystery deck with one modifier, at 75%); the structural
// Construction v2 shape runs with its Cake + Runic pair.
//
//   npx esbuild wasm-port/scripts/deck_pass.ts --bundle --platform=node --format=esm --outfile=<dir>/dp.mjs
//   node <dir>/dp.mjs <repo>/wasm-port/web/public <wasm-node>/ndm_core.js <out dir> [--stages early,mid,end,max]
//        [--procs 20] [--beam 4] [--screen 1,8000] [--full 2,30000]
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { buildDeck, type RawDeck, type Deck } from "../web/src/lib/deck";
import type { ImplicitDef } from "../web/src/lib/implicits";
import { playstyleObjectives, type Objective } from "../web/src/lib/rework/objective";
import { buildJob, withBudget } from "../web/src/lib/rework/payload";
import { extend, mysteryPool, poolFor, stageRules } from "../web/src/lib/rework/rules";
import type { ReworkConfig, Stage, StageBundle } from "../web/src/lib/rework/types";

const args = process.argv.slice(2);
const opt = (k: string, d: string) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const [pub, wasmPath, outDir] = args;
const cfg = JSON.parse(readFileSync(`${pub}/config.json`, "utf8"));
const rw: ReworkConfig = cfg.rework;
const decksJson = JSON.parse(readFileSync(`${pub}/decks.json`, "utf8"));
const catalog: Record<string, ImplicitDef> = decksJson._implicits;
const deckmod: number = cfg.modes.wolds.deckmod ?? 0;
const bundles: Partial<Record<Stage, StageBundle>> = {};
const bundle = (s: Stage) => (bundles[s] ??= JSON.parse(readFileSync(`${pub}/rework/${s}.json`, "utf8")));

interface Task { stage: Stage; group: string; deckKey: string; keys: string[]; mystery: boolean; label: string }
interface DeckResult extends Task { best: string[]; score: number; screen: number; sets: [string, number][] }

function deckFor(t: Task): Deck {
  const raw = (decksJson.wolds as RawDeck[]).find((d) => d.key === t.deckKey);
  if (raw) return buildDeck(raw, deckmod);
  const x = (bundle(t.stage).extra_decks ?? []).find((d) => d.key === t.deckKey);
  if (!x) throw new Error(`deck ${t.deckKey} not found`);
  return buildDeck({ key: x.key, name: x.name, slots: x.slots, arcane_slots: x.arcane_slots, base_core_slots: x.core_slots - deckmod,
                     min_regular: 0, max_greed: 0, implicit: { kind: "mystery" } }, deckmod);
}

function objectives(s: Stage): Objective[] {
  return playstyleObjectives(bundle(s), false).filter((o) => o.kind === "balanced" || o.kind === "loot");
}

function tasks(stages: Stage[]): Task[] {
  const out: Task[] = [];
  for (const s of stages) {
    const st = rw.stages[s];
    for (const o of objectives(s)) {
      const group = o.kind === "loot" ? "Loot" : o.group;
      for (const d of decksJson.wolds as RawDeck[]) {
        if (d.implicit?.kind === "mystery") continue;
        out.push({ stage: s, group, deckKey: d.key, keys: catalog[d.key] ? [d.key] : [], mystery: false, label: d.name });
      }
      if (!st.mystery) continue;
      const myst = (decksJson.wolds as RawDeck[]).find((d) => d.implicit?.kind === "mystery");
      if (myst) for (const m of mysteryPool(catalog)) {
        out.push({ stage: s, group, deckKey: myst.key, keys: [m], mystery: true, label: `${myst.name} [${m}]` });
      }
      for (const x of bundle(s).extra_decks ?? []) {
        out.push({ stage: s, group, deckKey: x.key, keys: ["cake", "runic"], mystery: true, label: `${x.name} [cake+runic]` });
      }
    }
  }
  return out;
}

function runTask(t: Task, wasm: any, beam: number, screen: number[], full: number[]): DeckResult {
  const b = bundle(t.stage);
  const rules = stageRules(rw, t.stage);
  const obj = objectives(t.stage).find((o) => (o.kind === "loot" ? "Loot" : o.group) === t.group)!;
  const deck = deckFor(t);
  const ctx = { bundle: b, catalog, rules, objective: obj, stage: t.stage, levelCards: true };
  const scores = new Map<string, number>();
  let seed = 1 + (t.label.length * 7919 + t.group.length * 104729) % 100000;
  const score = (cores: string[]) => {
    const k = cores.join("+");
    if (!scores.has(k)) {
      const job = buildJob({ deck, implicitKeys: t.keys, mystery: t.mystery, cores }, ctx);
      scores.set(k, wasm.runSaTagged(withBudget(job, screen[1], screen[0], seed++, false)).score);
    }
    return scores.get(k)!;
  };
  score([]);
  const pool = poolFor(deck, rules.pool);
  let frontier: string[][] = [[]];
  for (let size = 1; size <= deck.core_slots; size++) {
    const sets = extend(frontier, deck, size, pool);
    if (!sets.length) break;
    sets.forEach(score);
    frontier = sets.sort((a, b2) => score(b2) - score(a)).slice(0, beam);
  }
  const ranked = [...scores.entries()].sort((a, b2) => b2[1] - a[1]);
  const best = ranked[0][0] ? ranked[0][0].split("+") : [];
  const job = buildJob({ deck, implicitKeys: t.keys, mystery: t.mystery, cores: best }, ctx);
  const fullScore = wasm.runSaTagged({ ...withBudget(job, full[1], full[0], seed++, true) }).score;
  return { ...t, best, score: Math.max(fullScore, ranked[0][1]), screen: ranked[0][1], sets: ranked };
}

function child(taskFile: string, outFile: string) {
  const require = createRequire(import.meta.url);
  const wasm = require(wasmPath);
  const list: Task[] = JSON.parse(readFileSync(taskFile, "utf8"));
  const beam = Number(opt("--beam", "4"));
  const screen = opt("--screen", "1,8000").split(",").map(Number);
  const full = opt("--full", "2,30000").split(",").map(Number);
  const out: DeckResult[] = [];
  for (const t of list) {
    out.push(runTask(t, wasm, beam, screen, full));
    writeFileSync(outFile, JSON.stringify(out));
    process.stdout.write(".");
  }
}

function summarise(stage: Stage, results: DeckResult[]) {
  const groups: Record<string, unknown> = {};
  for (const g of [...new Set(results.map((r) => r.group))]) {
    const rs = results.filter((r) => r.group === g).sort((a, b) => b.score - a.score);
    const picks: Record<string, number> = {}, gainSum: Record<string, number> = {}, gainN: Record<string, number> = {};
    for (const r of rs) {
      for (const c of r.best) picks[c] = (picks[c] ?? 0) + 1;
      for (const c of rw.pool) {
        const withC = r.sets.filter(([k]) => k.split("+").includes(c));
        if (!withC.length) continue;
        const without = r.sets.filter(([k]) => !k.split("+").includes(c));
        const d = withC[0][1] - (without.length ? without[0][1] : withC[0][1]);
        gainSum[c] = (gainSum[c] ?? 0) + d;
        gainN[c] = (gainN[c] ?? 0) + 1;
      }
    }
    const gain = Object.fromEntries(Object.keys(gainSum).map((c) => [c, gainSum[c] / gainN[c]]));
    const scale = Math.max(1e-9, Math.abs(rs[0]?.score ?? 1));
    groups[g] = {
      n_decks: rs.length, picks, gain, gain_rel: Object.fromEntries(Object.entries(gain).map(([c, v]) => [c, v / scale])),
      decks: rs.map((r) => ({ label: r.label, deck: r.deckKey, keys: r.keys, mystery: r.mystery, cores: r.best, score: r.score })),
    };
  }
  return { stage, generated: new Date().toISOString().slice(0, 16).replace("T", " "), groups };
}

async function master() {
  const stages = opt("--stages", "early,mid,end,max").split(",") as Stage[];
  const procs = Number(opt("--procs", "20"));
  if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
  const all = tasks(stages);
  all.sort((a, b) => (b.mystery ? 0 : 1) - (a.mystery ? 0 : 1));
  const slices: Task[][] = Array.from({ length: procs }, () => []);
  all.forEach((t, i) => slices[i % procs].push(t));
  const self = fileURLToPath(import.meta.url);
  console.log(`[deck_pass] ${all.length} deck searches on ${procs} processes`);
  const t0 = Date.now();
  await Promise.all(slices.map((sl, i) => new Promise<void>((resolve, reject) => {
    const tf = `${outDir}/tasks_${i}.json`, of = `${outDir}/part_${i}.json`;
    writeFileSync(tf, JSON.stringify(sl));
    const p = spawn(process.execPath, [self, pub, wasmPath, outDir, ...args.slice(3), "--child", tf, of], { stdio: ["ignore", "inherit", "inherit"] });
    p.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`child ${i} exited ${code}`))));
  })));
  const results: DeckResult[] = [];
  for (let i = 0; i < procs; i++) results.push(...JSON.parse(readFileSync(`${outDir}/part_${i}.json`, "utf8")));
  for (const s of stages) {
    writeFileSync(`${outDir}/cores_pass_${s}.json`, JSON.stringify(summarise(s, results.filter((r) => r.stage === s))));
  }
  console.log(`\n[deck_pass] done in ${((Date.now() - t0) / 60000).toFixed(1)} min`);
}

const ci = args.indexOf("--child");
if (ci >= 0) child(args[ci + 1], args[ci + 2]);
else await master();
