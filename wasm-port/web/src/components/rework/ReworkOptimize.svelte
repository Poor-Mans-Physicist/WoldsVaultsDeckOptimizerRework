<script lang="ts">
  // Optimize tab of the rework site: inputs on the left, the deck in the centre, the picked cards and the deck's
  // totals on the right, a short guide at the bottom.
  import { onMount } from "svelte";
  import { buildDeck, type Deck } from "../../lib/deck";
  import type { ImplicitDef } from "../../lib/implicits";
  import { TYPE_LABEL } from "../../lib/palette";
  import type { CardType } from "../../lib/types";
  import { playstyleObjectives, type Objective } from "../../lib/rework/objective";
  import type { JobContext } from "../../lib/rework/payload";
  import { analyse, statLabel, type ResultView } from "../../lib/rework/results";
  import { manualRules, mysteryPool, roll, stageRules } from "../../lib/rework/rules";
  import { DEPTHS, SearchCancelled, searchRework, type DeckChoice, type SearchProgress } from "../../lib/rework/search";
  import type { AllowedTypes } from "../../lib/rework/stacks";
  import { STAGES, STAGE_LABEL, type ReworkConfig, type RuleIn, type Stage, type StageBundle } from "../../lib/rework/types";
  import { dispatchTagged, workerPoolSize } from "../../lib/workerPool";
  import type { Placed } from "../../lib/rework/search";
  import ReworkGrid from "./ReworkGrid.svelte";
  import ResultPanel from "./ResultPanel.svelte";
  import HowTo from "./HowTo.svelte";

  interface Props {
    decks: Deck[];
    catalog: Record<string, ImplicitDef>;
    cfg: ReworkConfig;
    baseUrl: string;
    deckmod: number;
  }
  let { decks: baseDecks, catalog, cfg, baseUrl, deckmod }: Props = $props();

  type Mode = "global" | "targeted" | "exact";
  const STORE = "wvdo-rework-settings-v1";
  const CORE_LABEL = (c: string) => (c === "color" ? "Colour" : c[0].toUpperCase() + c.slice(1));
  const MULT = new Set(["void", "empyreal", "arsenal", "aegis", "tool", "pluto"]);

  let mode: Mode = $state("global");
  let stage: Stage = $state("max");
  let valueStage: Stage = $state("max");
  let objectiveLabel = $state("Balanced (all builds)");
  let deckKey = $state("");
  let bestDeck = $state(false);
  let ownedDecks: string[] = $state([]);
  let freePair = $state(true);
  let pairA = $state("cake");
  let pairB = $state("runic");
  let ownedCores: string[] = $state([]);
  let coreValues: Record<string, number> = $state({});
  let greedValue = $state(1.5);
  let empyrealSlots = $state(3);
  let levelCards = $state(true);
  let depth: keyof typeof DEPTHS = $state("default");
  let allowed: AllowedTypes = $state({});
  let rules: RuleIn[] = $state([]);
  let exactCards: { key: string; label: string; n: number }[] = $state([]);

  let bundles: Partial<Record<Stage, StageBundle>> = $state.raw({});
  let running = $state(false);
  let progress: SearchProgress | null = $state(null);
  let error: string | null = $state(null);
  let view: ResultView | null = $state(null);
  let viewDeck: Deck | null = $state(null);
  let ranking: { label: string; cores: string[]; score: number }[] = $state([]);
  let runInfo = $state("");
  let abort: AbortController | null = null;

  const curStage = $derived(mode === "global" ? stage : valueStage);
  // Regular decks plus the structural shapes the data bundles carry (from any loaded stage).
  const decks: Deck[] = $derived.by(() => {
    const extra = new Map<string, Deck>();
    for (const b of Object.values(bundles)) {
      for (const x of b?.extra_decks ?? []) {
        if (extra.has(x.key)) continue;
        extra.set(x.key, buildDeck({ key: x.key, name: x.name, slots: x.slots, arcane_slots: x.arcane_slots,
                                     base_core_slots: x.core_slots - deckmod, min_regular: 0, max_greed: 0,
                                     implicit: x.mystery ? { kind: "mystery" } : null }, deckmod));
      }
    }
    return [...baseDecks, ...extra.values()];
  });
  const bundle = $derived(bundles[curStage] ?? null);
  const objectives = $derived(bundle ? playstyleObjectives(bundle, mode !== "global") : []);
  const deck = $derived(decks.find((d) => d.key === deckKey) ?? decks[0] ?? null);
  const isMystery = $derived(deck?.implicit?.kind === "mystery");
  const mysteryOk = $derived(mode !== "global" || cfg.stages[stage].mystery);
  const mods = $derived(mysteryPool(catalog));
  const limits = $derived(mode === "global" ? cfg.stages[stage].limits : {});

  function load() {
    try {
      const raw = localStorage.getItem(STORE);
      if (!raw) return;
      const s = JSON.parse(raw);
      mode = s.mode ?? mode; stage = s.stage ?? stage; valueStage = s.valueStage ?? valueStage;
      objectiveLabel = s.objectiveLabel ?? objectiveLabel; deckKey = s.deckKey ?? deckKey;
      bestDeck = !!s.bestDeck; ownedDecks = s.ownedDecks ?? ownedDecks; freePair = s.freePair ?? freePair;
      pairA = s.pairA ?? pairA; pairB = s.pairB ?? pairB; ownedCores = s.ownedCores ?? ownedCores;
      coreValues = s.coreValues ?? coreValues; greedValue = s.greedValue ?? greedValue;
      empyrealSlots = s.empyrealSlots ?? empyrealSlots; levelCards = s.levelCards ?? levelCards;
      depth = s.depth ?? depth; allowed = s.allowed ?? allowed; rules = s.rules ?? rules;
      exactCards = s.exactCards ?? exactCards;
    } catch (e) {
      console.error("[rework] FALLBACK: saved settings unreadable; defaults used", e);
    }
  }

  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify({ mode, stage, valueStage, objectiveLabel, deckKey, bestDeck, ownedDecks,
        freePair, pairA, pairB, ownedCores, coreValues, greedValue, empyrealSlots, levelCards, depth, allowed, rules,
        exactCards }));
    } catch {
      /* storage unavailable (private window): settings simply are not remembered */
    }
  }

  async function ensureBundle(s: Stage): Promise<StageBundle> {
    const have = bundles[s];
    if (have) return have;
    const res = await fetch(`${baseUrl}rework/${s}.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(`Failed to load the ${s} data bundle: ${res.status}`);
    const b = (await res.json()) as StageBundle;
    bundles = { ...bundles, [s]: b };
    return b;
  }

  onMount(async () => {
    load();
    if (!ownedCores.length) ownedCores = [...cfg.pool];
    for (const c of cfg.pool) if (coreValues[c] === undefined) coreValues[c] = roll(cfg, c, "max");
    if (!deckKey && decks.length) deckKey = (decks.find((d) => d.name === "The Rook Deck") ?? decks[0]).key;
    if (!ownedDecks.length) ownedDecks = decks.map((d) => d.key);
    try {
      await ensureBundle(curStage);
      await ensureBundle("max");
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  });

  $effect(() => {
    const s = curStage;
    ensureBundle(s).catch((e) => (error = e instanceof Error ? e.message : String(e)));
  });

  $effect(() => {
    if (objectives.length && !objectives.some((o) => o.label === objectiveLabel)) objectiveLabel = objectives[0].label;
  });

  $effect(() => { save(); });

  function toggle(list: string[], v: string): string[] {
    return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
  }

  function deckChoice(d: Deck): DeckChoice | null {
    if (d.implicit?.kind === "mystery") {
      if (!mysteryOk) return null;
      return freePair ? { deck: d, implicitKeys: [], mystery: true, freePair: true }
                      : { deck: d, implicitKeys: [pairA, pairB], mystery: true, freePair: false };
    }
    const keys = catalog[d.key] ? [d.key] : [];
    return { deck: d, implicitKeys: keys, mystery: false, freePair: false };
  }

  // ── Exact card builder ───────────────────────────────────────────────────
  type ExactType = "row" | "col" | "surr" | "diag" | "typeless" | "deluxe" | "greed" | "knack" | "arcane" | "battery";
  let exType: ExactType = $state("row");
  let exCard = $state("");
  let exColor = $state("red");
  let exGreed = $state("dir_greed_up");
  let exN = $state(1);
  const exOptions = $derived.by(() => {
    if (!bundle) return [] as { id: string; label: string }[];
    if (["row", "col", "surr", "diag"].includes(exType)) return bundle.cards.evo.map((c) => ({ id: c.id, label: statLabel(c.attr) }));
    if (exType === "typeless") return bundle.cards.typeless.map((c) => ({ id: c.id, label: statLabel(c.attr) }));
    if (exType === "deluxe") return bundle.cards.deluxe.map((c) => ({ id: c.id, label: statLabel(c.attr) }));
    if (exType === "knack") return bundle.special.knack.filter((c) => c.grant).map((c) => ({ id: c.id, label: `${c.grant![1]} +${c.grant![2]}` }));
    if (exType === "arcane") return bundle.special.arcane.filter((c) => c.grant).map((c) => ({ id: c.id, label: `${c.grant![1]} +${c.grant![2]}` }));
    if (exType === "battery") return ["Resource", "Knack", "Temporal"].map((g) => ({ id: g, label: `${g} card` }));
    return [];
  });
  function addExact() {
    let key: string, label: string;
    const opt = exOptions.find((o) => o.id === exCard) ?? exOptions[0];
    if (exType === "greed") { key = `greed|${exGreed}|${exGreed}|red`; label = `Greed ${exGreed.replace("dir_greed_", "")}`; }
    else if (!opt) return;
    else if (["row", "col", "surr", "diag"].includes(exType)) { key = `evo|${opt.id}|${exType}|${exColor}`; label = `${opt.label} ${TYPE_LABEL[exType as CardType]} (${exColor})`; }
    else if (exType === "typeless" || exType === "deluxe") { key = `${exType}|${opt.id}|${exType}|${exColor}`; label = `${opt.label} ${exType} (${exColor})`; }
    else if (exType === "knack") { key = `knack|${opt.id}|typeless|red`; label = `Knack ${opt.label}`; }
    else if (exType === "arcane") { key = `arcane|${opt.id}|arcane|red`; label = `Arcane ${opt.label}`; }
    else { key = `battery|${opt.id}|typeless|red`; label = opt.label; }
    const have = exactCards.find((e) => e.key === key);
    exactCards = have ? exactCards.map((e) => (e.key === key ? { ...e, n: e.n + exN } : e))
                      : [...exactCards, { key, label, n: exN }];
  }

  // ── Targeted rules ──────────────────────────────────────────────────────
  const RULE_KEYS: Record<RuleIn["axis"], string[]> = {
    group: ["Knack", "Temporal", "Resource", "Offensive", "Defensive", "Physical", "Magical", "Utility", "Foil"],
    type: ["row", "col", "surr", "diag", "typeless", "deluxe", "arcane", "dir_greed_up", "dir_greed_down", "dir_greed_left", "dir_greed_right"],
    color: ["red", "green", "blue", "yellow"],
    greed: [""],
  };
  function addRule() { rules = [...rules, { axis: "group", key: "Knack", min: 0, max: null }]; }

  async function run() {
    error = null;
    if (!deck) return;
    running = true;
    progress = null;
    abort = new AbortController();
    try {
      const b = await ensureBundle(curStage);
      const owned = ownedCores.filter((c) => cfg.pool.includes(c));
      const rs = mode === "global" ? stageRules(cfg, stage)
        : manualRules(cfg, Object.fromEntries(owned.map((c) => [c, coreValues[c] ?? roll(cfg, c, valueStage)])),
                      greedValue, empyrealSlots);
      const objective: Objective | undefined = playstyleObjectives(b, mode !== "global").find((o) => o.label === objectiveLabel);
      if (!objective) throw new Error("Pick a playstyle first.");
      const pool = mode === "exact" || !bestDeck ? [deck] : decks.filter((d) => ownedDecks.includes(d.key));
      const choices = pool.map(deckChoice).filter((c): c is DeckChoice => c !== null);
      if (!choices.length) throw new Error(isMystery && !mysteryOk ? "Mystery decks are only available at Max." : "No usable deck selected.");
      if (mode === "exact" && !exactCards.length) throw new Error("Add the cards you own first.");
      const ctx: JobContext = {
        bundle: b, catalog, rules: rs, objective, stage: curStage, levelCards,
        allowed: mode === "targeted" ? allowed : undefined,
        extraRules: mode === "targeted" ? rules.filter((r) => r.max !== null || r.min > 0) : undefined,
        exact: mode === "exact" ? new Map(exactCards.map((e) => [e.key, e.n])) : undefined,
      };
      const res = await searchRework({ ctx, decks: choices, owned, depth: DEPTHS[depth], seed: 20261020,
                                       onProgress: (p) => (progress = p), signal: abort.signal,
                                       runner: (p, w) => dispatchTagged<{ assignment: Placed[]; score: number }>(w, p),
                                       lanes: workerPoolSize() });
      view = await analyse(res.best, objective, curStage);
      viewDeck = res.best.spec.deck;
      ranking = res.ranking;
      runInfo = `${res.jobs} searches in ${res.secs.toFixed(1)} s`;
    } catch (e) {
      if (!(e instanceof SearchCancelled)) {
        error = e instanceof Error ? e.message : String(e);
        console.error("[rework] run failed", e);
      }
    } finally {
      running = false;
      abort = null;
    }
  }

  function modName(key: string): string {
    return (catalog[key]?.name ?? key).replace(/ Deck Modifier$/, "");
  }

  function fmtCore(c: string): string {
    const v = mode === "global" ? roll(cfg, c, stage) : (coreValues[c] ?? 0);
    return MULT.has(c) ? `×${(1 + v).toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}` : `+${(v * 100).toFixed(1).replace(/\.0$/, "")}%`;
  }
</script>

<div class="rw-layout">
  <aside class="rw-left">
    <section class="card">
      <div class="seg" role="tablist" aria-label="Optimizer mode">
        {#each [["global", "Global"], ["targeted", "Targeted"], ["exact", "Exact"]] as [m, lab] (m)}
          <button role="tab" aria-selected={mode === m} class:on={mode === m} onclick={() => (mode = m as Mode)}>{lab}</button>
        {/each}
      </div>
      {#if mode === "global"}
        <label class="field">Game stage
          <select bind:value={stage}>
            {#each STAGES as s (s)}<option value={s}>{STAGE_LABEL[s]}</option>{/each}
          </select>
        </label>
        <div class="hint">Greed cards +{Math.round(cfg.stages[stage].greed * 100)}%, cores at {cfg.stages[stage].rolls.replace("_", " ")} rolls{cfg.stages[stage].mystery ? ", Mystery decks allowed" : ""}{Object.keys(limits).length ? `, limits: ${Object.entries(limits).map(([k, v]) => `${v} ${k}`).join(", ")}` : ""}.</div>
      {:else}
        <label class="field">Value curves from
          <select bind:value={valueStage}>
            {#each STAGES as s (s)}<option value={s}>{STAGE_LABEL[s]}</option>{/each}
          </select>
        </label>
        <div class="row2">
          <label class="field">Greed card bonus<input type="number" step="0.05" min="0" bind:value={greedValue} /></label>
          <label class="field">Empyreal slots<input type="number" step="1" min="0" max="5" bind:value={empyrealSlots} /></label>
        </div>
      {/if}
      <label class="field">Playstyle
        <select bind:value={objectiveLabel}>
          {#each objectives as o (o.label)}<option value={o.label}>{o.label}</option>{/each}
        </select>
      </label>
    </section>

    <section class="card">
      <label class="field">Deck
        <select bind:value={deckKey} disabled={bestDeck && mode !== "exact"}>
          {#each decks as d (d.key)}<option value={d.key}>{d.name}</option>{/each}
        </select>
      </label>
      {#if mode !== "exact"}
        <label class="check"><input type="checkbox" bind:checked={bestDeck} /> Search all my decks</label>
        {#if bestDeck}
          <details>
            <summary>My decks ({ownedDecks.length})</summary>
            <div class="list">
              {#each decks as d (d.key)}
                <label class="check"><input type="checkbox" checked={ownedDecks.includes(d.key)}
                  onchange={() => (ownedDecks = toggle(ownedDecks, d.key))} /> {d.name}</label>
              {/each}
            </div>
          </details>
        {/if}
      {/if}
      {#if (isMystery || bestDeck) && mysteryOk}
        <label class="check"><input type="checkbox" bind:checked={freePair} /> Search the Mystery modifier pair</label>
        {#if !freePair}
          <div class="row2">
            <select bind:value={pairA} aria-label="Mystery modifier 1">{#each mods as m (m)}<option value={m}>{m}</option>{/each}</select>
            <select bind:value={pairB} aria-label="Mystery modifier 2">{#each mods as m (m)}<option value={m}>{m}</option>{/each}</select>
          </div>
        {/if}
      {:else if isMystery}
        <div class="hint warn">Mystery decks are only available at Max.</div>
      {/if}
    </section>

    <section class="card">
      <div class="head"><span>My cores</span>
        <button class="link" onclick={() => (ownedCores = ownedCores.length === cfg.pool.length ? [] : [...cfg.pool])}>
          {ownedCores.length === cfg.pool.length ? "none" : "all"}</button></div>
      <div class="cores">
        {#each cfg.pool as c (c)}
          <div class="core">
            <label class="check"><input type="checkbox" checked={ownedCores.includes(c)} onchange={() => (ownedCores = toggle(ownedCores, c))} />
              <span class="cname">{CORE_LABEL(c)}</span></label>
            {#if mode === "global"}
              <span class="cval">{fmtCore(c)}</span>
            {:else}
              <input class="cinput" type="number" step="0.005" aria-label="{CORE_LABEL(c)} value" bind:value={coreValues[c]} />
            {/if}
          </div>
        {/each}
      </div>
      {#if mode !== "global"}<div class="hint">Values as the bonus: Arsenal ×1.5 is 0.5.</div>{/if}
    </section>

    {#if mode === "targeted"}
      <section class="card">
        <div class="head"><span>Allowed cards</span></div>
        <div class="toggles">
          {#each [["evo", "Evolving"], ["typeless", "Typeless"], ["deluxe", "Deluxe"], ["greed", "Greed"], ["arcane", "Arcane"], ["knack", "Knack"], ["battery", "Resource/Temporal"]] as [k, lab] (k)}
            <label class="check"><input type="checkbox" checked={allowed[k as keyof AllowedTypes] !== false}
              onchange={(e) => (allowed = { ...allowed, [k]: (e.currentTarget as HTMLInputElement).checked })} /> {lab}</label>
          {/each}
        </div>
        <div class="head"><span>Tag rules</span><button class="link" onclick={addRule}>add</button></div>
        {#each rules as r, i (i)}
          <div class="rule">
            <select bind:value={r.axis} aria-label="Rule axis">
              <option value="group">Tag</option><option value="type">Type</option><option value="color">Colour</option><option value="greed">Greed total</option>
            </select>
            {#if r.axis !== "greed"}
              <select bind:value={r.key} aria-label="Rule key">{#each RULE_KEYS[r.axis] as k (k)}<option value={k}>{k.replace("dir_greed_", "greed ")}</option>{/each}</select>
            {:else}<span></span>{/if}
            <input type="number" min="0" aria-label="Minimum" bind:value={r.min} />
            <input type="number" min="0" aria-label="Maximum" placeholder="max" value={r.max ?? ""}
              oninput={(e) => { const v = (e.currentTarget as HTMLInputElement).value; r.max = v === "" ? null : Number(v); }} />
            <button class="x" aria-label="Remove rule" onclick={() => (rules = rules.filter((_, j) => j !== i))}>×</button>
          </div>
        {/each}
      </section>
    {/if}

    {#if mode === "exact"}
      <section class="card">
        <div class="head"><span>My cards</span>{#if exactCards.length}<button class="link" onclick={() => (exactCards = [])}>clear</button>{/if}</div>
        <div class="row2">
          <select bind:value={exType} aria-label="Card type">
            {#each ["row", "col", "surr", "diag", "typeless", "deluxe", "greed", "knack", "arcane", "battery"] as t (t)}<option value={t}>{t === "battery" ? "resource/temporal" : t}</option>{/each}
          </select>
          {#if exType === "greed"}
            <select bind:value={exGreed} aria-label="Greed direction">{#each ["dir_greed_up", "dir_greed_down", "dir_greed_left", "dir_greed_right"] as g (g)}<option value={g}>{g.replace("dir_greed_", "")}</option>{/each}</select>
          {:else}
            <select bind:value={exCard} aria-label="Card">{#each exOptions as o (o.id)}<option value={o.id}>{o.label}</option>{/each}</select>
          {/if}
        </div>
        <div class="row3">
          {#if ["row", "col", "surr", "diag", "typeless", "deluxe"].includes(exType)}
            <select bind:value={exColor} aria-label="Colour">{#each ["red", "green", "blue", "yellow"] as c (c)}<option value={c}>{c}</option>{/each}</select>
          {:else}<span></span>{/if}
          <input type="number" min="1" aria-label="Count" bind:value={exN} />
          <button onclick={addExact}>Add</button>
        </div>
        <div class="list">
          {#each exactCards as e (e.key)}
            <div class="exrow"><span class="exl">{e.label}</span><span>×{e.n}</span>
              <button class="x" aria-label="Remove" onclick={() => (exactCards = exactCards.filter((x) => x.key !== e.key))}>×</button></div>
          {/each}
        </div>
      </section>
    {/if}

    <section class="card">
      <label class="check"><input type="checkbox" bind:checked={levelCards} /> Use Knack and Arcane level cards</label>
      <div class="seg small" role="radiogroup" aria-label="Search depth">
        {#each Object.entries(DEPTHS) as [k, d] (k)}
          <button role="radio" aria-checked={depth === k} class:on={depth === k} onclick={() => (depth = k as keyof typeof DEPTHS)}>{d.label}</button>
        {/each}
      </div>
      {#if running}
        <button class="run cancel" onclick={() => abort?.abort()}>Cancel</button>
      {:else}
        <button class="run" onclick={run} disabled={!bundle || !deck}>Optimize</button>
      {/if}
      <div class="progress" aria-live="polite">
        {#if running && progress}
          <div class="bar"><div style:width="{progress.total ? (100 * progress.done) / progress.total : 0}%"></div></div>
          <div class="ptext">{progress.phase} · {progress.done}/{progress.total}</div>
        {:else if error}
          <div class="ptext err">{error}</div>
        {:else if runInfo}
          <div class="ptext">{runInfo}</div>
        {/if}
      </div>
    </section>
  </aside>

  <section class="rw-center">
    <div class="card center-card">
      {#if viewDeck && view}
        <div class="title">{view.deckName}{view.implicits.length ? ` · ${view.implicits.map(modName).join(" + ")}` : ""}</div>
        <ReworkGrid deck={viewDeck} slots={view.slots} />
      {:else if deck}
        <div class="title">{deck.name}</div>
        <ReworkGrid {deck} slots={null} />
      {/if}
    </div>
    {#if ranking.length}
      <div class="card">
        <div class="head"><span>Best core sets found</span></div>
        {#each ranking as r, i (i)}
          <div class="rank"><span class="rl">{r.label}</span><span class="rc">{r.cores.map(CORE_LABEL).join(" + ") || "no cores"}</span><span>{r.score.toFixed(view?.objective.kind === "loot" ? 0 : 3)}</span></div>
        {/each}
        <div class="hint">Screening scores (short searches); the winner was re-run in full.</div>
      </div>
    {/if}
  </section>

  <aside class="rw-right">
    <ResultPanel {view} {modName} />
  </aside>
</div>

<HowTo meta={bundle?.meta ?? null} />

<style>
  .rw-layout {
    display: grid; grid-template-columns: 300px minmax(0, 1fr) 340px; gap: 16px; padding: 16px; align-items: start;
  }
  @media (max-width: 1100px) { .rw-layout { grid-template-columns: 300px minmax(0, 1fr); } .rw-right { grid-column: 1 / -1; } }
  @media (max-width: 720px) { .rw-layout { grid-template-columns: minmax(0, 1fr); } }
  .rw-left, .rw-center, .rw-right { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; min-width: 0; }
  .center-card { overflow-x: auto; }
  .title { font-weight: 600; margin-bottom: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-secondary); margin-top: 10px; min-width: 0; }
  select, input[type="number"] {
    background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border); border-radius: 5px;
    padding: 5px 6px; font: inherit; font-size: 13px; min-width: 0; width: 100%; box-sizing: border-box;
  }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
  .row3 { display: grid; grid-template-columns: 1fr 70px auto; gap: 8px; margin-top: 8px; }
  .seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 2px; background: var(--bg-input); border-radius: 6px; padding: 2px; }
  .seg button { background: transparent; color: var(--text-secondary); border: 0; border-radius: 5px; padding: 6px 0; font: inherit; cursor: pointer; }
  .seg button.on { background: var(--accent); color: white; }
  .seg.small { margin-top: 10px; }
  .hint { font-size: 12px; color: var(--text-muted); margin-top: 6px; line-height: 1.35; }
  .hint.warn { color: #F59E0B; }
  .check { display: flex; align-items: center; gap: 6px; font-size: 13px; margin-top: 8px; min-width: 0; }
  .head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; text-transform: uppercase;
          letter-spacing: .06em; color: var(--text-secondary); margin-top: 4px; }
  .link { background: none; border: 0; color: var(--accent); cursor: pointer; font: inherit; font-size: 12px; text-transform: none; }
  .cores { display: grid; grid-template-columns: 1fr; gap: 2px; margin-top: 6px; }
  .core { display: grid; grid-template-columns: minmax(0, 1fr) 86px; align-items: center; gap: 6px; }
  .core .check { margin-top: 0; }
  .cname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cval { text-align: right; font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
  .cinput { padding: 3px 5px; font-size: 12px; }
  .toggles { display: grid; grid-template-columns: 1fr 1fr; }
  .rule { display: grid; grid-template-columns: 1fr 1fr 52px 52px 22px; gap: 4px; margin-top: 6px; align-items: center; }
  .x { background: none; border: 0; color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 0; }
  .list { max-height: 220px; overflow-y: auto; margin-top: 6px; }
  .exrow { display: grid; grid-template-columns: minmax(0, 1fr) auto 22px; gap: 6px; font-size: 13px; padding: 2px 0; }
  .exl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  details summary { cursor: pointer; font-size: 13px; margin-top: 6px; color: var(--text-secondary); }
  .run { width: 100%; margin-top: 12px; padding: 9px 0; border: 0; border-radius: 6px; background: var(--accent); color: white;
         font: inherit; font-weight: 600; cursor: pointer; }
  .run:disabled { opacity: .5; cursor: default; }
  .run.cancel { background: #475569; }
  .progress { height: 34px; margin-top: 8px; }
  .bar { height: 6px; background: var(--bg-input); border-radius: 3px; overflow: hidden; }
  .bar div { height: 100%; background: var(--accent); transition: width .2s; }
  .ptext { font-size: 12px; color: var(--text-secondary); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ptext.err { color: #F87171; white-space: normal; }
  .rank { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) 64px; gap: 8px; font-size: 13px; padding: 2px 0;
          font-variant-numeric: tabular-nums; }
  .rank span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rank span:last-child { text-align: right; }
  .rc { color: var(--text-secondary); }
</style>
