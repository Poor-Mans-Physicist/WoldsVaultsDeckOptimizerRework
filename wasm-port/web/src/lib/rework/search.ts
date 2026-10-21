// Rework deck search on the worker pool: a beam over subsets of the owned cores for each candidate deck (as DeckFAST
// scripts/deck_menus.py does), Mystery modifier pairs when the player has not fixed them, then full two-phase runs on
// the best core sets. Jobs run through a bounded queue so a run can be cancelled between jobs.

import type { Deck } from "../deck";
import { buildJob, withBudget, type BuiltJob, type JobContext, type JobSpec } from "./payload";
import { extend, mysteryPool, poolFor } from "./rules";

export interface DepthPreset {
  label:  string;
  screen: [number, number];
  full:   [number, number];
  beam:   number;
  final:  number;
  pairs:  number;
}

export const DEPTHS: Record<"fast" | "default" | "deep", DepthPreset> = {
  fast:    { label: "Fast",    screen: [1, 10000], full: [2, 30000], beam: 3, final: 2, pairs: 6 },
  default: { label: "Default", screen: [2, 20000], full: [4, 40000], beam: 6, final: 3, pairs: 12 },
  deep:    { label: "Deep",    screen: [2, 30000], full: [8, 60000], beam: 8, final: 4, pairs: 16 },
};

export interface DeckChoice {
  deck:         Deck;
  implicitKeys: string[];
  mystery:      boolean;
  /** Mystery deck without a fixed pair: search the modifier pair too. */
  freePair:     boolean;
}

export interface Placed {
  t: string; color: string; scale_color: string; groups: string[]; stack: number;
}

export interface Candidate {
  spec:   JobSpec;
  job:    BuiltJob;
  score:  number;
  result: { assignment: Placed[]; score: number };
}

export interface SearchProgress {
  phase: string;
  done:  number;
  total: number;
}

export interface SearchRequest {
  ctx:        JobContext;
  decks:      DeckChoice[];
  owned:      string[];
  depth:      DepthPreset;
  seed:       number;
  onProgress?: (p: SearchProgress) => void;
  signal?:    AbortSignal;
  /** Job runner and parallel lanes: the browser worker pool on the site, the WASM kernel directly in Node scripts. */
  runner:     (payload: Record<string, unknown>, lane: number) => Promise<{ assignment: Placed[]; score: number }>;
  lanes:      number;
}

export interface SearchResult {
  best:       Candidate;
  ranking:    { label: string; cores: string[]; score: number }[];
  jobs:       number;
  secs:       number;
}

export class SearchCancelled extends Error {
  constructor() { super("search cancelled"); }
}

/** Run payloads on the pool with at most one in flight per worker; results in input order. */
async function runQueue(payloads: Record<string, unknown>[], phase: string, req: SearchRequest,
                        counter: { jobs: number }): Promise<{ assignment: Placed[]; score: number }[]> {
  const n = Math.max(1, req.lanes);
  const run = req.runner;
  const out: { assignment: Placed[]; score: number }[] = new Array(payloads.length);
  let next = 0, done = 0;
  req.onProgress?.({ phase, done: 0, total: payloads.length });
  const lane = async (w: number) => {
    while (next < payloads.length) {
      if (req.signal?.aborted) throw new SearchCancelled();
      const i = next++;
      out[i] = await run(payloads[i], w);
      counter.jobs++;
      req.onProgress?.({ phase, done: ++done, total: payloads.length });
    }
  };
  await Promise.all(Array.from({ length: Math.min(n, payloads.length) }, (_, w) => lane(w)));
  return out;
}

const specKey = (s: JobSpec) => `${s.deck.key}|${s.implicitKeys.join("+")}|${s.cores.join("+")}`;

function label(s: JobSpec): string {
  return s.mystery && s.implicitKeys.length ? `${s.deck.name} [${s.implicitKeys.join(" + ")}]` : s.deck.name;
}

export async function searchRework(req: SearchRequest): Promise<SearchResult> {
  const t0 = performance.now();
  const counter = { jobs: 0 };
  const { ctx, depth } = req;
  const owned = new Set(req.owned);
  const scores = new Map<string, { spec: JobSpec; job: BuiltJob; score: number }>();
  let seed = req.seed;

  const screen = async (specs: JobSpec[], phase: string) => {
    const todo = specs.filter((s) => !scores.has(specKey(s)));
    const jobs = todo.map((s) => buildJob(s, ctx));
    const res = await runQueue(jobs.map((j) => withBudget(j, depth.screen[1], depth.screen[0], seed++, false)),
                               phase, req, counter);
    todo.forEach((s, i) => scores.set(specKey(s), { spec: s, job: jobs[i], score: res[i].score }));
  };

  const beam = async (choices: { deck: Deck; implicitKeys: string[]; mystery: boolean }[], phase: string) => {
    const frontier = new Map(choices.map((c) => [c, [[]] as string[][]]));
    const top = Math.max(...choices.map((c) => c.deck.core_slots));
    for (let size = 1; size <= top; size++) {
      const specs: JobSpec[] = [];
      for (const c of choices) {
        if (size > c.deck.core_slots) continue;
        const pool = poolFor(c.deck, ctx.rules.pool.filter((x) => owned.has(x)));
        for (const cores of extend(frontier.get(c)!, c.deck, size, pool)) specs.push({ ...c, cores });
      }
      if (!specs.length) break;
      await screen(specs, `${phase}: ${size}-core sets`);
      for (const c of choices) {
        const mine = [...scores.values()].filter((v) => v.spec.deck.key === c.deck.key
          && v.spec.implicitKeys.join("+") === c.implicitKeys.join("+") && v.spec.cores.length === size)
          .sort((a, b) => b.score - a.score);
        if (mine.length) frontier.set(c, mine.slice(0, depth.beam).map((v) => v.spec.cores));
      }
    }
    await screen(choices.map((c) => ({ ...c, cores: [] })), `${phase}: no cores`);
  };

  const fixed = req.decks.filter((d) => !d.freePair);
  if (fixed.length) await beam(fixed, "Core sets");
  for (const d of req.decks.filter((x) => x.freePair)) {
    if (!ctx.rules.mysteryAllowed) {
      console.error(`[rework] FALLBACK: Mystery decks are not allowed at this stage; ${d.deck.name} skipped`);
      continue;
    }
    const mods = mysteryPool(ctx.catalog);
    await beam(mods.map((m) => ({ deck: d.deck, implicitKeys: [m], mystery: true })), "Mystery modifiers");
    const single = new Map(mods.map((m) => [m, Math.max(...[...scores.values()]
      .filter((v) => v.spec.deck.key === d.deck.key && v.spec.implicitKeys.length === 1 && v.spec.implicitKeys[0] === m)
      .map((v) => v.score))]));
    const lead = [...mods].sort((a, b) => single.get(b)! - single.get(a)!).slice(0, 10);
    const pairs: [string, string][] = [];
    for (let i = 0; i < lead.length; i++) for (let j = i + 1; j < lead.length; j++) pairs.push([lead[i], lead[j]]);
    pairs.sort((a, b) => (single.get(b[0])! + single.get(b[1])!) - (single.get(a[0])! + single.get(a[1])!));
    await beam(pairs.slice(0, depth.pairs).map((p) => ({ deck: d.deck, implicitKeys: p, mystery: true })),
               "Mystery pairs");
  }
  if (!scores.size) throw new Error("rework: nothing to search (no decks selected)");

  const ranked = [...scores.values()].sort((a, b) => b.score - a.score);
  const finals = ranked.slice(0, Math.max(depth.final, 1));
  const full = await runQueue(finals.map((f) => withBudget(f.job, depth.full[1], depth.full[0], seed++, true)),
                              "Full runs", req, counter);
  let best: Candidate | null = null;
  finals.forEach((f, i) => {
    const c: Candidate = { spec: f.spec, job: f.job, score: full[i].score, result: full[i] };
    if (!best || c.score > best.score) best = c;
  });
  return {
    best: best!,
    ranking: ranked.slice(0, 12).map((v) => ({ label: label(v.spec), cores: v.spec.cores, score: v.score })),
    jobs: counter.jobs,
    secs: (performance.now() - t0) / 1000,
  };
}
