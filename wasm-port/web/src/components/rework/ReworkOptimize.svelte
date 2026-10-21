<script lang="ts">
  // Optimize tab of the rework site: inputs on the left (optimizer, deck, cores), the deck and the how-to guide in the
  // centre, the picked cards and the deck's totals on the right.
  import { onMount } from "svelte";
  import { buildDeck, type Deck } from "../../lib/deck";
  import type { ImplicitDef } from "../../lib/implicits";
  import { TYPE_LABEL } from "../../lib/palette";
  import type { CardType, Position } from "../../lib/types";
  import {
    canRemoveConstructionTile, constructionCandidates, effectiveDeck, emptyStructural, maxArcaneConvert,
    maxConstruction, pruneConvertedSlots, structuralCoreCost, type StructuralCores,
  } from "../../lib/structural";
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
  const MODES: [Mode, string, string][] = [
    ["global", "Global", "A game stage's rules: greed cards, core rolls, card tier and limits."],
    ["targeted", "Targeted", "Your own core values and greed bonus, with per-tag limits."],
    ["exact", "Exact", "Only the exact cards you own."],
  ];

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
  let bonusCores: number | null = $state(null);
  let levelCards = $state(true);
  let depth: keyof typeof DEPTHS = $state("default");
  let allowed: AllowedTypes = $state({});
  let rules: RuleIn[] = $state([]);
  let exactCards: { key: string; label: string; n: number }[] = $state([]);
  let structural: StructuralCores = $state(emptyStructural());

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
  const bonus = $derived(bonusCores ?? deckmod);
  const structOn = $derived(!bestDeck || (mode as Mode) === "exact");
  const coreSlots = (d: Deck) => Math.max(0, d.base_core_slots + bonus - (d.key === deck?.key && structOn ? structuralCoreCost(structural) : 0));
  /** The deck the search sees: structural cores applied, core slots from Bonus Cores and the structural cost. */
  const effDeck: Deck | null = $derived.by(() => {
    if (!deck) return null;
    const d = structOn ? effectiveDeck(deck, structural, deckmod) : deck;
    return { ...d, core_slots: coreSlots(deck) };
  });
  const candidates = $derived(deck && structOn && !view ? constructionCandidates(deck, structural) : []);
  const placementsLeft = $derived(Math.max(0, maxConstruction(structural) - structural.addedSlots.length));
  const conversionsLeft = $derived(Math.max(0, maxArcaneConvert(structural) - structural.convertedSlots.length));
  const depthInfo = $derived(DEPTHS[depth]);

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
      exactCards = s.exactCards ?? exactCards; bonusCores = s.bonusCores ?? bonusCores;
      structural = s.structural ?? structural;
    } catch (e) {
      console.error("[rework] FALLBACK: saved settings unreadable; defaults used", e);
    }
  }

  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify({ mode, stage, valueStage, objectiveLabel, deckKey, bestDeck, ownedDecks,
        freePair, pairA, pairB, ownedCores, coreValues, greedValue, empyrealSlots, levelCards, depth, allowed, rules,
        exactCards, bonusCores, structural }));
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

  function pickDeck(k: string) {
    if (k === deckKey) return;
    deckKey = k;
    structural = { ...emptyStructural(), greaterStructural: structural.greaterStructural };
    view = null;
  }

  // ── Structural cores (Construction / Arcane): reshape the chosen deck before a run ───────────────────────────────
  function structuralChanged(next: StructuralCores) {
    structural = next;
    view = null;
  }
  function setConstruction(on: boolean) {
    if (!deck) return;
    const next = { ...structural, constructionEnabled: on, addedSlots: on ? structural.addedSlots : [] };
    structuralChanged({ ...next, convertedSlots: pruneConvertedSlots(deck, next) });
  }
  function setArcaneCore(on: boolean) {
    structuralChanged({ ...structural, arcaneCoreEnabled: on, convertedSlots: on ? structural.convertedSlots : [] });
  }
  function placeSlot(p: Position) {
    if (structural.addedSlots.length >= maxConstruction(structural)) return;
    structuralChanged({ ...structural, addedSlots: [...structural.addedSlots, p] });
  }
  function removeSlot(p: Position) {
    if (!deck || !canRemoveConstructionTile(p, deck, structural)) return;
    const next = { ...structural, addedSlots: structural.addedSlots.filter((q) => q[0] !== p[0] || q[1] !== p[1]) };
    structuralChanged({ ...next, convertedSlots: pruneConvertedSlots(deck, next) });
  }
  function convertSlot(p: Position) {
    if (!structural.arcaneCoreEnabled || structural.convertedSlots.length >= maxArcaneConvert(structural)) return;
    if (structural.convertedSlots.some((q) => q[0] === p[0] && q[1] === p[1])) return;
    structuralChanged({ ...structural, convertedSlots: [...structural.convertedSlots, p] });
  }
  function unconvertSlot(p: Position) {
    structuralChanged({ ...structural, convertedSlots: structural.convertedSlots.filter((q) => q[0] !== p[0] || q[1] !== p[1]) });
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
    if (!deck || !effDeck) return;
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
      const pool = mode === "exact" || !bestDeck ? [effDeck]
        : decks.filter((d) => ownedDecks.includes(d.key)).map((d) => ({ ...d, core_slots: coreSlots(d) }));
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

  /** A core's value as the in-game % (multiplicative cores as their bonus: x1.5 shows 50). */
  function corePct(c: string): string {
    const v = mode === "global" ? roll(cfg, c, stage) : (coreValues[c] ?? 0);
    return String(Math.round(v * 10000) / 100);
  }
  function setCorePct(c: string, raw: string) {
    const v = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(v)) coreValues = { ...coreValues, [c]: v / 100 };
  }
</script>

<div class="rw-layout">
  <aside class="rw-left">
    <section class="card">
      <h3>Optimizer</h3>
      <div class="seg-label">Mode</div>
      <div class="seg" role="radiogroup" aria-label="Optimizer mode">
        {#each MODES as [m, lab, hint] (m)}
          <button type="button" role="radio" aria-checked={mode === m} class="seg-btn" class:active={mode === m}
            title={hint} onclick={() => { mode = m; view = null; }}>{lab}</button>
        {/each}
      </div>

      <div class="seg-label">{mode === "global" ? "Game stage" : "Value curves from"}</div>
      <div class="seg" role="radiogroup" aria-label={mode === "global" ? "Game stage" : "Value curves from"}>
        {#each STAGES as s (s)}
          {@const on = (mode === "global" ? stage : valueStage) === s}
          <button type="button" role="radio" aria-checked={on} class="seg-btn" class:active={on}
            onclick={() => { if (mode === "global") stage = s; else valueStage = s; }}>{STAGE_LABEL[s]}</button>
        {/each}
      </div>
      <div class="meta">
        {#if mode === "global"}
          Greed +{Math.round(cfg.stages[stage].greed * 100)}% · {cfg.stages[stage].rolls.replace("_", " ")} rolls{cfg.stages[stage].mystery ? " · Mystery" : ""}{Object.keys(limits).length ? ` · ${Object.entries(limits).map(([k, v]) => `${v} ${k}`).join(", ")}` : ""}
        {:else}
          Scores use this stage's ledger curves; your core values set the rules.
        {/if}
      </div>

      <label class="field">Playstyle
        <select bind:value={objectiveLabel}>
          {#each objectives as o (o.label)}<option value={o.label}>{o.label}</option>{/each}
        </select>
      </label>

      <div class="seg-label">Depth</div>
      <div class="seg" role="radiogroup" aria-label="Search depth">
        {#each Object.entries(DEPTHS) as [k, d] (k)}
          <button type="button" role="radio" aria-checked={depth === k} class="seg-btn" class:active={depth === k}
            onclick={() => (depth = k as keyof typeof DEPTHS)}>{d.label}</button>
        {/each}
      </div>
      <div class="meta">
        screens {depthInfo.screen[0]}×{depthInfo.screen[1].toLocaleString()} · full {depthInfo.full[0]}×{depthInfo.full[1].toLocaleString()} · beam {depthInfo.beam}
      </div>

      <label class="check-row"><input type="checkbox" bind:checked={levelCards} /> <span>Knack and Arcane level cards</span></label>

      <div class="run-row">
        {#if running}
          <button class="run cancel" type="button" onclick={() => abort?.abort()}>Cancel</button>
        {:else}
          <button class="run" type="button" onclick={run} disabled={!bundle || !deck}>Optimize</button>
        {/if}
      </div>
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

    <section class="card">
      <h3>Deck</h3>
      <select aria-label="Deck" value={deckKey} disabled={bestDeck && mode !== "exact"}
        onchange={(e) => pickDeck((e.currentTarget as HTMLSelectElement).value)}>
        {#each decks as d (d.key)}<option value={d.key}>{d.name}</option>{/each}
      </select>
      {#if effDeck}
        <div class="meta">{effDeck.slots.length} slots · {effDeck.arcaneSlots.length} arcane · {effDeck.core_slots} core slot{effDeck.core_slots === 1 ? "" : "s"}</div>
      {/if}
      {#if mode !== "exact"}
        <label class="check-row"><input type="checkbox" bind:checked={bestDeck} /> <span>Search all my decks</span></label>
        {#if bestDeck}
          <details>
            <summary>My decks ({ownedDecks.length})</summary>
            <div class="list">
              {#each decks as d (d.key)}
                <label class="check-row"><input type="checkbox" checked={ownedDecks.includes(d.key)}
                  onchange={() => (ownedDecks = toggle(ownedDecks, d.key))} /> <span>{d.name}</span></label>
              {/each}
            </div>
          </details>
        {/if}
      {/if}
      {#if (isMystery || bestDeck) && mysteryOk}
        <label class="check-row"><input type="checkbox" bind:checked={freePair} /> <span>Search the Mystery modifier pair</span></label>
        {#if !freePair}
          <div class="row2">
            <select bind:value={pairA} aria-label="Mystery modifier 1">{#each mods as m (m)}<option value={m}>{modName(m)}</option>{/each}</select>
            <select bind:value={pairB} aria-label="Mystery modifier 2">{#each mods as m (m)}<option value={m}>{modName(m)}</option>{/each}</select>
          </div>
        {/if}
      {:else if isMystery}
        <div class="meta warn">Mystery decks are only available at Max.</div>
      {/if}
    </section>

    <section class="card">
      <header class="card-head">
        <h3>Cores</h3>
        <div class="btns">
          <button type="button" class="btn-flat primary" onclick={() => (ownedCores = [...cfg.pool])}>Enable all</button>
          <button type="button" class="btn-flat" onclick={() => (ownedCores = [])}>Disable all</button>
        </div>
      </header>
      {#each cfg.pool as c (c)}
        <div class="row">
          <label class="check">
            <input type="checkbox" checked={ownedCores.includes(c)} onchange={() => (ownedCores = toggle(ownedCores, c))} />
            <span>{CORE_LABEL(c)}{MULT.has(c) ? " ×" : ""}</span>
          </label>
          <span class="override-wrap">
            <input type="number" class="override" step="any" aria-label="{CORE_LABEL(c)} value" readonly={mode === "global"}
              value={corePct(c)} oninput={(e) => setCorePct(c, (e.currentTarget as HTMLInputElement).value)} />
            <span class="pct" aria-hidden="true">%</span>
          </span>
        </div>
      {/each}
      <div class="hint">{mode === "global" ? "Values are the stage's rolls." : "Type your cores' rolls."} Cores marked × multiply: 50% means ×1.5.</div>

      <div class="bonus-row">
        <div class="bonus-label">
          <label for="rw-bonus-cores">Bonus Cores</label>
          <details class="info">
            <summary aria-label="What is Bonus Cores?">?</summary>
            <div class="info-body">
              <p>Extra core slots beyond what the deck provides. In Wold's, Core Expertise crafts a deck with one extra slot,
                so the default is <code>{deckmod}</code>.</p>
              <p>Negative values reserve slots for cores the optimizer doesn't consider.</p>
            </div>
          </details>
        </div>
        <input id="rw-bonus-cores" type="number" class="override" step="1" value={bonus}
          oninput={(e) => { const v = Number((e.currentTarget as HTMLInputElement).value); bonusCores = Number.isFinite(v) ? v : null; }} />
      </div>

      <div class="bonus-row tight">
        <div class="bonus-label"><label for="rw-greed">Greed card bonus</label></div>
        <span class="override-wrap">
          <input id="rw-greed" type="number" class="override" step="any" readonly={mode === "global"}
            value={String(Math.round((mode === "global" ? cfg.stages[stage].greed : greedValue) * 10000) / 100)}
            oninput={(e) => { const v = Number((e.currentTarget as HTMLInputElement).value); if (Number.isFinite(v)) greedValue = v / 100; }} />
          <span class="pct" aria-hidden="true">%</span>
        </span>
      </div>
      {#if mode !== "global"}
        <div class="bonus-row tight">
          <div class="bonus-label"><label for="rw-empyreal">Empyreal slots</label></div>
          <input id="rw-empyreal" type="number" class="override" step="1" min="0" max="5" bind:value={empyrealSlots} />
        </div>
      {/if}

      <div class="struct-section">
        <div class="struct-head">Structural</div>
        {#if !structOn}
          <div class="hint flat">Structural cores reshape one deck; turn off "Search all my decks" to use them.</div>
        {:else}
          <div class="row">
            <label class="check">
              <input type="checkbox" checked={structural.greaterStructural}
                onchange={(e) => structuralChanged({ ...structural, greaterStructural: (e.currentTarget as HTMLInputElement).checked })} />
              <span>Greater structural cores</span>
            </label>
          </div>
          <div class="hint flat">Greater cores roll 4–5 slots: caps go from 3 to 5. Each structural core uses a core slot.</div>
          <div class="row">
            <label class="check">
              <input type="checkbox" checked={structural.constructionEnabled}
                onchange={(e) => setConstruction((e.currentTarget as HTMLInputElement).checked)} />
              <span>Construction Core</span>
            </label>
            {#if structural.constructionEnabled}<span class="counter" class:done={placementsLeft === 0}>{placementsLeft} left</span>{/if}
          </div>
          {#if structural.constructionEnabled}
            <div class="hint">Click a <span class="hint-plus">+</span> tile to add it · right-click an added tile to remove.</div>
          {/if}
          <div class="row">
            <label class="check">
              <input type="checkbox" checked={structural.arcaneCoreEnabled}
                onchange={(e) => setArcaneCore((e.currentTarget as HTMLInputElement).checked)} />
              <span>Arcane Core</span>
            </label>
            {#if structural.arcaneCoreEnabled}<span class="counter" class:done={conversionsLeft === 0}>{conversionsLeft} left</span>{/if}
          </div>
          {#if structural.arcaneCoreEnabled}
            <div class="hint">Click a regular slot to convert it to arcane · right-click to revert.</div>
          {/if}
        {/if}
      </div>
    </section>

    {#if mode === "targeted"}
      <section class="card">
        <h3>Allowed cards</h3>
        <div class="toggles">
          {#each [["evo", "Evolving"], ["typeless", "Typeless"], ["deluxe", "Deluxe"], ["greed", "Greed"], ["arcane", "Arcane"], ["knack", "Knack"], ["battery", "Resource/Temporal"]] as [k, lab] (k)}
            <label class="check-row"><input type="checkbox" checked={allowed[k as keyof AllowedTypes] !== false}
              onchange={(e) => (allowed = { ...allowed, [k]: (e.currentTarget as HTMLInputElement).checked })} /> <span>{lab}</span></label>
          {/each}
        </div>
        <header class="card-head spaced"><h3>Tag rules</h3><button type="button" class="btn-flat primary" onclick={addRule}>Add</button></header>
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
            <button type="button" class="x" aria-label="Remove rule" onclick={() => (rules = rules.filter((_, j) => j !== i))}>×</button>
          </div>
        {/each}
      </section>
    {/if}

    {#if mode === "exact"}
      <section class="card">
        <header class="card-head"><h3>My cards</h3>{#if exactCards.length}<button type="button" class="btn-flat" onclick={() => (exactCards = [])}>Clear</button>{/if}</header>
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
          <button type="button" class="add" onclick={addExact}>Add</button>
        </div>
        <div class="list">
          {#each exactCards as e (e.key)}
            <div class="exrow"><span class="exl">{e.label}</span><span>×{e.n}</span>
              <button type="button" class="x" aria-label="Remove" onclick={() => (exactCards = exactCards.filter((x) => x.key !== e.key))}>×</button></div>
          {/each}
        </div>
      </section>
    {/if}
  </aside>

  <section class="rw-center">
    <div class="card center-card">
      {#if viewDeck && view}
        <div class="title">{view.deckName}{view.implicits.length ? ` · ${view.implicits.map(modName).join(" + ")}` : ""}</div>
        <ReworkGrid deck={viewDeck} slots={view.slots} added={structOn ? structural.addedSlots : []}
          converted={structOn ? structural.convertedSlots : []} />
      {:else if effDeck}
        <div class="title">{effDeck.name}</div>
        <ReworkGrid deck={effDeck} slots={null} {candidates}
          added={structOn ? structural.addedSlots : []} converted={structOn ? structural.convertedSlots : []}
          canConvert={structOn && structural.arcaneCoreEnabled && conversionsLeft > 0}
          onPlace={placeSlot} onRemove={removeSlot} onConvert={convertSlot} onUnconvert={unconvertSlot} />
      {/if}
      {#if view && structOn && (structural.constructionEnabled || structural.arcaneCoreEnabled)}
        <div class="meta center">Change a structural core to edit the layout again; the result clears.</div>
      {/if}
    </div>
    {#if ranking.length}
      <div class="card">
        <h3>Best core sets found</h3>
        {#each ranking as r, i (i)}
          <div class="rank"><span class="rl">{r.label}</span><span class="rc">{r.cores.map(CORE_LABEL).join(" + ") || "no cores"}</span><span>{r.score.toFixed(view?.objective.kind === "loot" ? 0 : 3)}</span></div>
        {/each}
        <div class="meta">Screening scores (short searches); the winner was re-run in full.</div>
      </div>
    {/if}
    <HowTo meta={bundle?.meta ?? null} />
  </section>

  <aside class="rw-right">
    <ResultPanel {view} {modName} />
  </aside>
</div>

<style>
  .rw-layout {
    display: grid; grid-template-columns: 280px minmax(0, 1fr) 340px; gap: 16px; padding: 16px; align-items: start;
  }
  @media (max-width: 1100px) { .rw-layout { grid-template-columns: 280px minmax(0, 1fr); } .rw-right { grid-column: 1 / -1; } }
  @media (max-width: 720px) { .rw-layout { grid-template-columns: minmax(0, 1fr); } }
  .rw-left, .rw-center, .rw-right { display: flex; flex-direction: column; gap: 12px; min-width: 0; }

  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 12px; min-width: 0; }
  .card h3 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; letter-spacing: .04em; }
  .card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .card-head h3 { margin: 0; }
  .card-head.spaced { margin-top: 10px; }
  .center-card { overflow-x: auto; }
  .title { font-weight: 600; margin-bottom: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .seg-label { font-size: 11px; color: var(--text-muted); margin: 8px 0 4px; text-transform: uppercase; letter-spacing: .04em; }
  .seg { display: flex; background: var(--bg-input); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .seg-btn { flex: 1; min-width: 0; padding: 5px 0; font-size: 12px; background: transparent; color: var(--text-secondary);
             border: 0; cursor: pointer; font-family: inherit; }
  .seg-btn + .seg-btn { border-left: 1px solid var(--border); }
  .seg-btn:hover { color: var(--text-primary); }
  .seg-btn.active { background: var(--accent); color: #fff; font-weight: 600; }
  .meta { font-size: 11px; color: var(--text-muted); margin: 4px 0 2px; line-height: 1.35; }
  .meta.warn { color: #F59E0B; }
  .meta.center { text-align: center; margin-top: 8px; }

  .field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-secondary); margin-top: 10px; min-width: 0; }
  select, input[type="number"] {
    background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border); border-radius: 4px;
    padding: 5px 7px; font: inherit; font-size: 13px; min-width: 0; width: 100%; box-sizing: border-box;
  }
  .check-row { display: flex; align-items: center; gap: 6px; font-size: 13px; margin-top: 8px; min-width: 0; cursor: pointer; }
  .check-row span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
  .row3 { display: grid; grid-template-columns: 1fr 64px auto; gap: 8px; margin-top: 8px; }

  .run-row { display: flex; gap: 6px; margin-top: 10px; }
  .run { flex-grow: 1; padding: 8px 0; background: var(--accent); color: #fff; border: 0; border-radius: 6px;
         font-size: 14px; font-weight: 600; cursor: pointer; font-family: inherit; }
  .run:disabled { opacity: .6; cursor: not-allowed; }
  .run.cancel { background: #475569; }
  .progress { height: 32px; margin-top: 6px; }
  .bar { height: 6px; background: var(--bg-input); border-radius: 3px; overflow: hidden; }
  .bar div { height: 100%; background: var(--accent); transition: width .2s; }
  .ptext { font-size: 12px; color: var(--text-secondary); margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ptext.err { color: #FCA5A5; white-space: normal; }

  .btns { display: flex; gap: 2px; }
  .btn-flat { background: transparent; border: 0; cursor: pointer; font-size: 12px; text-transform: uppercase; font-weight: 600;
              padding: 4px 8px; border-radius: 4px; color: var(--text-secondary); font-family: inherit; }
  .btn-flat.primary { color: var(--accent); }
  .btn-flat:hover { background: var(--bg-hover); }
  .row { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
  .check { display: flex; align-items: center; gap: 6px; flex-grow: 1; font-size: 13px; cursor: pointer; min-width: 0; }
  .check span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .override { width: 100px; padding: 4px 6px; font-size: 13px; text-align: right; flex: none; }
  .override[readonly] { color: var(--text-secondary); background: transparent; }
  .override-wrap { position: relative; display: inline-flex; flex: none; }
  .override-wrap .override { padding-right: 22px; }
  .override-wrap .pct { position: absolute; right: 7px; top: 50%; transform: translateY(-50%); font-size: 12px;
                        color: var(--text-muted); pointer-events: none; }
  .hint { font-size: 11px; color: var(--text-muted); padding: 0 0 4px 22px; line-height: 1.4; }
  .hint.flat { padding-left: 0; }
  .bonus-row { display: flex; align-items: center; gap: 8px; padding-top: 10px; margin-top: 8px; border-top: 1px solid var(--border); }
  .bonus-row.tight { border-top: 0; padding-top: 4px; margin-top: 0; }
  .bonus-label { display: flex; align-items: center; gap: 6px; flex-grow: 1; font-size: 13px; min-width: 0; }
  .info { position: relative; }
  .info summary { list-style: none; cursor: pointer; width: 18px; height: 18px; line-height: 16px; text-align: center;
                  border-radius: 50%; border: 1px solid var(--border); color: var(--text-secondary); font-size: 12px;
                  font-weight: 600; background: var(--bg-input); box-sizing: border-box; }
  .info summary::-webkit-details-marker { display: none; }
  .info[open] > summary { color: var(--accent); border-color: var(--accent); }
  .info[open] > .info-body { position: absolute; top: 22px; left: 0; width: 240px; z-index: 10; padding: 10px 12px;
                             background: var(--bg-card); border: 1px solid var(--border); border-radius: 4px; font-size: 12px;
                             line-height: 1.45; box-shadow: 0 4px 12px rgba(0,0,0,.4); }
  .info-body p { margin: 0; }
  .info-body p + p { margin-top: 6px; }
  .info-body code { background: var(--bg-input); padding: 1px 4px; border-radius: 3px; font-size: 11px; }
  .struct-section { padding-top: 10px; margin-top: 8px; border-top: 1px solid var(--border); }
  .struct-head { font-size: 11px; text-transform: uppercase; color: var(--text-secondary); font-weight: 600; letter-spacing: .04em; margin-bottom: 6px; }
  .counter { font-size: 11px; color: var(--accent); padding: 2px 8px; border-radius: 999px; background: var(--bg-hover); flex: none; }
  .counter.done { color: var(--text-muted); }
  .hint-plus { display: inline-block; width: 14px; height: 14px; line-height: 12px; text-align: center; background: #4B5563;
               color: #F1F5F9; border-radius: 3px; font-weight: 600; font-size: 10px; }

  .toggles { display: grid; grid-template-columns: 1fr 1fr; column-gap: 8px; }
  .toggles .check-row { margin-top: 4px; }
  .rule { display: grid; grid-template-columns: 1fr 1fr 50px 50px 20px; gap: 4px; margin-top: 6px; align-items: center; }
  .x { background: none; border: 0; color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 0; }
  .add { background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border); border-radius: 4px;
         padding: 0 10px; cursor: pointer; font-family: inherit; }
  .list { max-height: 220px; overflow-y: auto; margin-top: 6px; }
  .exrow { display: grid; grid-template-columns: minmax(0, 1fr) auto 20px; gap: 6px; font-size: 13px; padding: 2px 0; }
  .exl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  details summary { cursor: pointer; font-size: 13px; margin-top: 6px; color: var(--text-secondary); }

  .rank { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) 64px; gap: 8px; font-size: 13px; padding: 2px 0;
          font-variant-numeric: tabular-nums; }
  .rank span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rank span:last-child { text-align: right; }
  .rc { color: var(--text-secondary); }
</style>
