// Structural optimizer (web only): searches every Construction Core layout
// the game can roll for the player's deck, with the run's core combo and
// every other scoring input held fixed, and reports how rare the player's
// own layout is.
//
// Pipeline (all kernel work in the worker pool, see construction.rs):
//   1. enumerate  — every reachable layout for the roll's k range, grouped
//                   into reflection classes with exact per-roll probabilities
//   2. screen     — every class scored once with a cheap cold SA
//   3. search     — outer layout-space SA chains (whole chain in wasm)
//   4. refine     — finalists re-scored deep; best rendered as a full result
//   5. rarity     — P(one roll beats the player's layout): classes the
//                   screen already proves better + the band just below the
//                   player's score (closest classes refined outright, the
//                   rest estimated by probability-weighted sampling)
//
// Every score is an achieved assignment, i.e. a lower bound on that layout's
// optimum, so cached scores only ever improve. The cache survives until any
// scoring input changes (fingerprint); moving construction slots keeps it.

import type { Deck } from "./deck";
import type { CoreSpec, Position } from "./types";
import { OptimizerMode } from "./types";
import { activeImplicits, buildTaggedPayload, type TaggedRunInput } from "./tagged";
import { dispatchConstruction, dispatchTagged, workerPoolSize } from "./workerPool";
import { finalizeTaggedResult, type TaggedOptimizeResult } from "./taggedClient";
import type { RawTaggedPlaced } from "./optimize";

export type StructEffort = "standard" | "deep";

interface EffortParams {
  chainsPerWorker: number;
  chainSteps:      number;
  chainIter:       number;
  screenIter:      number;
  screenRestarts:  number;
  refineTop:       number;
  refineIter:      number;
  refineRestarts:  number;
  bandIter:        number;
  bandRestarts:    number;
  bandDet:         number;
  bandSamples:     number;
  /** Second pass: undecided band classes re-scored at this budget. */
  resolveIter:     number;
  resolveMargin:   number;
}

/** Search depths, from the native lab study (scratch, 2026-09-24): outer SA
 *  at the Standard budget found the exhaustive optimum on 4/4 test decks.
 *  Rarity accuracy vs exhaustive truth (2 decks, 6 thresholds): the 10k×1
 *  screen gives median relative error 13–60 % on P(better); the 30k×2 screen
 *  (Deep) 0–22 %. */
export const STRUCT_EFFORT: Record<StructEffort, EffortParams> = {
  standard: {
    chainsPerWorker: 2, chainSteps: 300, chainIter: 20_000,
    screenIter: 10_000, screenRestarts: 1,
    refineTop: 10, refineIter: 150_000, refineRestarts: 12,
    bandIter: 100_000, bandRestarts: 4, bandDet: 200, bandSamples: 400,
    resolveIter: 200_000, resolveMargin: 0.02,
  },
  deep: {
    chainsPerWorker: 4, chainSteps: 500, chainIter: 30_000,
    screenIter: 30_000, screenRestarts: 2,
    refineTop: 16, refineIter: 300_000, refineRestarts: 24,
    bandIter: 100_000, bandRestarts: 4, bandDet: 300, bandSamples: 600,
    resolveIter: 400_000, resolveMargin: 0.015,
  },
};

/** Screen band: classes whose screen score is within this fraction below the
 *  player's score may still beat it (screen undershoot p99 ≈ 14–21 %). */
const BAND_MARGIN = 0.2;
/** Strata over the band (by screen score / player score) for the estimate. */
const BAND_STRATA = 5;
/** Band-refine undershoot (100k×4 p99 ≈ 2–5 %): refined scores this close
 *  below the player's may still truly beat it. */
const UNDECIDED_MARGIN = 0.03;

export interface StructInputs {
  /** Run input as the player's last optimize used it (deck ignored). */
  run:        TaggedRunInput;
  combo:      CoreSpec[];
  /** Stock deck with the player's arcane conversions, no construction slots. */
  baseDeck:   Deck;
  /** Builds the full deck for a set of added cells. */
  deckFor:    (added: Position[]) => Deck;
  userAdded:  Position[];
  /** The player's optimize score for their own layout. */
  userScore:  number;
  fingerprint: string;
}

export interface LayoutStat {
  added:    Position[];
  score:    number;
  /** P(one roll produces exactly this layout). */
  rollProb: number;
  /** Same, counting its mirror images (score-identical). */
  classProb: number;
}

export interface StructReport {
  tier:        string;
  ks:          number[];
  ideal:       LayoutStat;
  idealResult: TaggedOptimizeResult;
  user:        LayoutStat;
  /** user.score / ideal.score. */
  ofMax:       number;
  /** Estimated P(one roll beats the player's layout) + 1σ. */
  pBetter:     number;
  pBetterSe:   number;
  /** Range for P(better): sampling ±2σ plus the undecided mass. */
  pBetterLo:   number;
  pBetterHi:   number;
  /** Mass proven better by an achieved score (lower bound). */
  pBetterCert: number;
  /** P(one roll is at least as good as the player's layout). */
  pAtLeast:    number;
  classes:     number;
  sets:        number;
  elapsedMs:   number;
  fromCache:   boolean;
}

interface ClassRow { k: number; rep: number[]; members: number; prob: number }

interface Cache {
  fingerprint: string;
  classes:     ClassRow[];
  setCounts:   number[];
  /** Best achieved score per class (lower bound). */
  best:        Float64Array;
  /** Screen budget (iterations × restarts) already applied to every class. */
  screened:    number;
  /** Classes already refined at the band budget. */
  bandDone:    Uint8Array;
  /** Resolve budget (iterations) already applied per class (0 = none). */
  resolved:    Float64Array;
  /** Best achieved score per exact set key (chains, refines, the player). */
  sets:        Map<string, number>;
  idealKey:    string | null;
  idealResult: TaggedOptimizeResult | null;
  searchedAt:  StructEffort | null;
}

let _cache: Cache | null = null;

export function clearStructCache(): void { _cache = null; }

/** True when a finished pass for this scoring setup + tier is cached, so a
 *  hand-placed layout's report only needs the cheap rarity refinement. */
export function hasStructCache(fingerprint: string, k: number): boolean {
  const tier = tierFor(k);
  return tier !== null && _cache !== null && _cache.searchedAt !== null
    && _cache.fingerprint === `${fingerprint}|${tier.ks.join(",")}`;
}

/** The construction core's slot count is fixed on the core item (rolled
 *  once when the core is generated: lesser 1, normal 2–3, greater 4–5), and
 *  every anvil reroll places exactly that many — so the layout space is the
 *  placed count k only. The tier name is display-only. */
export function tierFor(k: number): { name: string; ks: number[] } | null {
  if (k === 1) return { name: "Lesser", ks: [1] };
  if (k === 2 || k === 3) return { name: "Normal", ks: [k] };
  if (k === 4 || k === 5) return { name: "Greater", ks: [k] };
  return null;
}

const idx = ([r, c]: Position) => r * 9 + c;
const pos = (i: number): Position => [Math.floor(i / 9), i % 9] as const;
const keyOf = (cells: number[]) => [...cells].sort((a, b) => a - b).join(",");

/** Reflections that keep every score identical (see construction.rs::canon).
 *  Exact stacks and directional Targeted rules can tell left from right. */
function symmetryFlags(run: TaggedRunInput): { allowX: boolean; allowY: boolean } {
  if (run.mode === OptimizerMode.EXACT) return { allowX: false, allowY: false };
  const ruled = (keys: string[]) => run.mode === OptimizerMode.TARGETED && run.targetedRules.some(
    (r) => r.axis === "type" && keys.includes(r.key) && (r.min !== null || r.max !== null));
  const allowX = !ruled(["dir_greed_left", "dir_greed_right"]);
  const cake = activeImplicits(run).some((i) => i.kind === "row_pos");
  const allowY = !cake && !ruled(["dir_greed_up", "dir_greed_down"]);
  return { allowX, allowY };
}

function baseCells(deck: Deck): [number, number, boolean][] {
  const arc = new Set(deck.arcaneSlots.map(([r, c]) => `${r},${c}`));
  return deck.slots.map(([r, c]) => [r, c, arc.has(`${r},${c}`)]);
}

/** Split `items` round-robin into `n` chunks (skip empties). */
function chunks<T>(items: T[], n: number): T[][] {
  const out: T[][] = Array.from({ length: Math.max(1, n) }, () => []);
  items.forEach((x, i) => out[i % out.length].push(x));
  return out.filter((c) => c.length > 0);
}

export async function runStructural(
  inp: StructInputs,
  effort: StructEffort,
  onProgress: (msg: string) => void,
): Promise<StructReport> {
  const t0 = performance.now();
  const P = STRUCT_EFFORT[effort];
  const pool = workerPoolSize();
  const k = inp.userAdded.length;
  const tier = tierFor(k);
  if (!tier) throw new Error(`${k} construction slots isn't a roll any Construction Core makes (1, 2–3 or 4–5).`);
  const kProbs = tier.ks.map(() => 1 / tier.ks.length);
  const { allowX, allowY } = symmetryFlags(inp.run);
  const base = baseCells(inp.baseDeck);
  const template = buildTaggedPayload({ ...inp.run, deck: inp.baseDeck }, inp.combo, 1);
  const common = { base, ks: tier.ks, k_probs: kProbs, allow_x: allowX, allow_y: allowY };
  const userCells = inp.userAdded.map(idx);
  const userKey = keyOf(userCells);

  // 1. enumerate (cached) + locate the player's layout
  const fingerprint = `${inp.fingerprint}|${tier.ks.join(",")}`;
  let fromCache = true;
  if (!_cache || _cache.fingerprint !== fingerprint) {
    fromCache = false;
    onProgress("Enumerating every possible roll…");
    const en = await dispatchConstruction<{ classes: ClassRow[]; set_counts: number[] }>(
      0, "cc_enum", { ...common, user_added: [] });
    _cache = {
      fingerprint, classes: en.classes, setCounts: en.set_counts,
      best: new Float64Array(en.classes.length).fill(-Infinity), screened: 0,
      bandDone: new Uint8Array(en.classes.length),
      resolved: new Float64Array(en.classes.length),
      sets: new Map(), idealKey: null, idealResult: null, searchedAt: null,
    };
  }
  const cache = _cache;
  const locate = async (cells: Position[]) => dispatchConstruction<{
    user_legal: boolean; user_prob: number; user_class: number | null;
  }>(0, "cc_enum", { ...common, user_added: cells.map(([r, c]) => [r, c]), classes_out: false });
  const userLoc = await locate(inp.userAdded);
  if (!userLoc.user_legal || userLoc.user_class === null) {
    throw new Error("This construction layout can't be rolled by the game (slots must grow orthogonally inside cols 0–8, rows 0–5).");
  }

  const evalSets = async (sets: number[][], nIter: number, restarts: number, seed: number) => {
    const parts = chunks(sets.map((s, i) => ({ s, i })), Math.min(sets.length, pool * 3));
    const out = new Array<number>(sets.length);
    await Promise.all(parts.map((part, w) =>
      dispatchConstruction<number[]>(w, "cc_eval", {
        template, base, sets: part.map((x) => x.s), n_iter: nIter, restarts, seed: seed + w * 7919,
      }).then((sc) => part.forEach((x, j) => { out[x.i] = sc[j]; })),
    ));
    return out;
  };
  /** Deep score one set with its restarts spread over the whole pool. */
  const deepScore = async (cells: number[], nIter: number, restarts: number, seed: number) => {
    const per = Math.max(1, Math.ceil(restarts / pool));
    const n = Math.ceil(restarts / per);
    const res = await Promise.all(Array.from({ length: n }, (_, w) =>
      dispatchConstruction<number[]>(w, "cc_eval", {
        template, base, sets: [cells], n_iter: nIter, restarts: per, seed: seed + w * 104729,
      })));
    return Math.max(...res.map((r) => r[0]));
  };
  const noteSet = (cells: number[], v: number) => {
    const key = keyOf(cells);
    cache.sets.set(key, Math.max(cache.sets.get(key) ?? -Infinity, v));
  };

  // 2. the player's own score (deep) — the rarity threshold
  onProgress("Scoring your layout deeply…");
  const userDeep = await deepScore(userCells, P.refineIter, P.refineRestarts, 11);
  noteSet(userCells, Math.max(userDeep, inp.userScore));
  const T = cache.sets.get(userKey)!;
  cache.best[userLoc.user_class] = Math.max(cache.best[userLoc.user_class], T);

  // 3. screen every class once (cached per budget)
  const screenBudget = P.screenIter * P.screenRestarts;
  if (cache.screened < screenBudget) {
    const reps = cache.classes.map((c) => c.rep);
    onProgress(`Screening ${reps.length.toLocaleString()} layouts…`);
    const sc = await evalSets(reps, P.screenIter, P.screenRestarts, 101 + screenBudget);
    sc.forEach((v, i) => { if (v > cache.best[i]) cache.best[i] = v; });
    cache.screened = screenBudget;
  }

  // 4. outer layout-space SA + deep refine of the finalists (cached per effort)
  const searched = cache.searchedAt === "deep" || cache.searchedAt === effort;
  if (!searched || !cache.idealResult) {
    const nChains = pool * P.chainsPerWorker;
    onProgress(`Searching layouts (${nChains} chains)…`);
    const chainRes = await Promise.all(Array.from({ length: nChains }, (_, c) =>
      dispatchConstruction<{ set: number[]; score: number }[]>(c % pool, "cc_chain", {
        template, base, k: tier.ks[c % tier.ks.length], steps: P.chainSteps,
        n_iter: P.chainIter, seed: 7000 + c * 31, keep: 8,
      })));
    for (const list of chainRes) for (const v of list) noteSet(v.set, v.score);

    const byClass = cache.classes.map((c, i) => ({ set: c.rep, score: cache.best[i] }))
      .sort((a, b) => b.score - a.score).slice(0, P.refineTop);
    const byChain = [...cache.sets.entries()].map(([key, score]) => ({
      set: key ? key.split(",").map(Number) : [], score,
    })).sort((a, b) => b.score - a.score).slice(0, P.refineTop);
    const finalists = new Map<string, number[]>();
    for (const f of [...byClass, ...byChain]) finalists.set(keyOf(f.set), f.set);
    finalists.set(userKey, userCells);
    onProgress(`Refining ${finalists.size} finalists…`);
    for (const [key, cells] of finalists) {
      if (key === userKey) continue;
      noteSet(cells, await deepScore(cells, P.refineIter, P.refineRestarts, 3 + cells[0]));
    }
    let bestKey = userKey;
    for (const key of finalists.keys()) {
      if ((cache.sets.get(key) ?? -Infinity) > (cache.sets.get(bestKey) ?? -Infinity)) bestKey = key;
    }
    cache.idealKey = bestKey;
    cache.idealResult = null;
    cache.searchedAt = effort;
  }

  // A hand-placed layout can beat the cached search's pick.
  if (cache.idealKey !== userKey && T > (cache.sets.get(cache.idealKey ?? "") ?? -Infinity)) {
    cache.idealKey = userKey;
    cache.idealResult = null;
  }

  // Render the ideal layout (full assignment + breakdown) once per search.
  const idealCells = cache.idealKey ? cache.idealKey.split(",").map(Number) : userCells;
  const idealAdded = idealCells.map(pos);
  if (!cache.idealResult) {
    onProgress("Rendering the ideal layout…");
    const deck = inp.deckFor(idealAdded);
    const run = { ...inp.run, deck };
    const per = Math.max(1, Math.ceil(P.refineRestarts / pool));
    const res = await Promise.all(Array.from({ length: Math.ceil(P.refineRestarts / per) }, (_, w) =>
      dispatchTagged<{ assignment: RawTaggedPlaced[]; score: number }>(
        w, { ...buildTaggedPayload(run, inp.combo, per), n_iter: P.refineIter, seed: 900 + w }),
    ));
    const top = res.reduce((a, b) => (b.score > a.score ? b : a));
    noteSet(idealCells, top.score);
    cache.idealResult = finalizeTaggedResult(run, inp.combo, top.assignment, top.score);
  }
  const idealScore = Math.max(cache.sets.get(keyOf(idealCells)) ?? -Infinity, cache.idealResult.wasmScore);

  // 5. rarity: P(one roll beats T). Classes already proven better count
  // exactly; the band just below T is resolved by refining its closest
  // classes outright and estimating the rest by stratified sampling.
  onProgress("Estimating roll rarity…");
  const better = (v: number) => v > T * (1 + 1e-9);
  const uc = userLoc.user_class;
  const refine = async (ids: number[], seed: number) => {
    const todo = ids.filter((i) => !cache.bandDone[i]);
    if (!todo.length) return;
    const sc = await evalSets(todo.map((i) => cache.classes[i].rep), P.bandIter, P.bandRestarts, seed);
    sc.forEach((v, j) => {
      const i = todo[j];
      if (v > cache.best[i]) cache.best[i] = v;
      cache.bandDone[i] = 1;
    });
  };
  const band: number[] = [];
  cache.classes.forEach((_, i) => {
    if (i !== uc && !better(cache.best[i]) && cache.best[i] >= T * (1 - BAND_MARGIN)) band.push(i);
  });
  band.sort((a, b) => cache.best[b] - cache.best[a]);
  const det = band.slice(0, P.bandDet);
  await refine(det, 303);
  const strata: number[][] = Array.from({ length: BAND_STRATA }, () => []);
  for (const i of band.slice(P.bandDet)) {
    const x = (1 - cache.best[i] / T) / BAND_MARGIN;
    strata[Math.min(BAND_STRATA - 1, Math.max(0, Math.floor(x * BAND_STRATA)))].push(i);
  }
  const perStratum = Math.floor(P.bandSamples / BAND_STRATA);
  let seed = 0x2545F491;
  const rnd = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return (seed >>> 0) / 4294967296; };
  const sampled = new Set<number>();
  const draws: { mass: number; picks: number[]; exact: boolean; members: number[] }[] = [];
  for (const st of strata) {
    if (!st.length) continue;
    const mass = st.reduce((a, i) => a + cache.classes[i].prob, 0);
    if (st.length <= perStratum) {
      st.forEach((i) => sampled.add(i));
      draws.push({ mass, picks: [], exact: true, members: st });
      continue;
    }
    const cum: number[] = [];
    let acc = 0;
    for (const i of st) { acc += cache.classes[i].prob; cum.push(acc); }
    const picks: number[] = [];
    for (let s = 0; s < perStratum; s++) {
      const x = rnd() * mass;
      let lo = 0, hi = cum.length - 1;
      while (lo < hi) { const m = (lo + hi) >> 1; if (cum[m] < x) lo = m + 1; else hi = m; }
      picks.push(st[lo]);
      sampled.add(st[lo]);
    }
    draws.push({ mass, picks, exact: false, members: st });
  }
  await refine([...sampled], 505);

  // Resolve pass: evaluated classes that landed just below T get a deeper
  // re-score, and afterwards a tighter undecided margin.
  const firstMargin = (v: number) => !better(v) && v >= T * (1 - UNDECIDED_MARGIN);
  const evaluated = [...new Set([...det, ...sampled])];
  const toResolve = evaluated.filter((i) => firstMargin(cache.best[i]) && cache.resolved[i] < P.resolveIter);
  if (toResolve.length) {
    onProgress(`Resolving ${toResolve.length} close calls…`);
    const sc = await evalSets(toResolve.map((i) => cache.classes[i].rep), P.resolveIter, P.bandRestarts, 707);
    sc.forEach((v, j) => {
      const i = toResolve[j];
      if (v > cache.best[i]) cache.best[i] = v;
      cache.resolved[i] = P.resolveIter;
    });
  }

  let pCert = 0;
  cache.classes.forEach((c, i) => { if (i !== uc && better(cache.best[i])) pCert += c.prob; });
  const inStrata = new Set(draws.flatMap((d) => d.members));
  // Refined scores still undershoot a little, so classes landing just below
  // T are undecided: their mass widens the range's upper end only. (After
  // the resolve pass they are almost never truly better — on the runic+cake
  // mystery test the point estimate without them was within 1 % / 7 % of
  // exhaustive truth.)
  const undecidedAt = (i: number) => !better(cache.best[i]) && cache.best[i] >= T * (1 - (cache.resolved[i] > 0 ? P.resolveMargin : UNDECIDED_MARGIN));
  let pEst = 0, pUnd = 0, pVar = 0;
  cache.classes.forEach((c, i) => {
    if (i === uc || inStrata.has(i)) return;
    if (better(cache.best[i])) pEst += c.prob;
    else if (undecidedAt(i)) pUnd += c.prob;
  });
  for (const d of draws) {
    if (d.exact) {
      for (const i of d.members) {
        if (better(cache.best[i])) pEst += cache.classes[i].prob;
        else if (undecidedAt(i)) pUnd += cache.classes[i].prob;
      }
      continue;
    }
    const n = d.picks.length;
    const f = d.picks.filter((i) => better(cache.best[i])).length / n;
    const g = d.picks.filter((i) => undecidedAt(i)).length / n;
    pEst += d.mass * f;
    pUnd += d.mass * g;
    pVar += d.mass * d.mass * f * (1 - f) / n;
  }
  const pCertOnly = pCert;
  const pSe = Math.sqrt(pVar);
  const pBetter = Math.max(pCert, pEst);
  const pBetterLo = Math.max(pCert, pEst - 2 * pSe);
  const pBetterHi = pEst + pUnd + 2 * pSe;
  const pRest = draws.reduce((a, d) => a + (d.exact ? 0 : d.mass), 0);
  const userClass = cache.classes[uc];
  let pTie = userClass.prob;
  cache.classes.forEach((c, i) => {
    if (i !== uc && !better(cache.best[i]) && cache.best[i] >= T * (1 - 1e-9)) pTie += c.prob;
  });

  const idealLoc = await locate(idealAdded);
  const report: StructReport = {
    tier: tier.name,
    ks: tier.ks,
    ideal: {
      added: idealAdded, score: idealScore, rollProb: idealLoc.user_prob,
      classProb: idealLoc.user_class !== null ? cache.classes[idealLoc.user_class].prob : idealLoc.user_prob,
    },
    idealResult: cache.idealResult,
    user: { added: inp.userAdded, score: T, rollProb: userLoc.user_prob, classProb: userClass.prob },
    ofMax: T / Math.max(idealScore, T),
    pBetter, pBetterSe: pSe, pBetterCert: pCertOnly, pBetterLo, pBetterHi,
    pAtLeast: Math.min(1, pBetter + pTie),
    classes: cache.classes.length,
    sets: cache.setCounts.reduce((a, b) => a + b, 0),
    elapsedMs: performance.now() - t0,
    fromCache,
  };
  console.log(
    `[structural] ${effort} ${report.elapsedMs.toFixed(0)}ms  classes=${report.classes} ` +
    `T=${T.toFixed(3)} ideal=${idealScore.toFixed(3)} pBetter=${pBetter.toExponential(3)} ` +
    `[${pBetterLo.toExponential(2)}, ${pBetterHi.toExponential(2)}] (cert ${pCertOnly.toExponential(3)}, undecided ${pUnd.toExponential(2)}, ` +
    `band ${band.length}, sampled-rest mass ${pRest.toExponential(2)})`,
  );
  return report;
}
