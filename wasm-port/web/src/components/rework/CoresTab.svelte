<script lang="ts">
  // Cores tab: every core and deck modifier with its reworked rolls and rules, how often the stored ledger builds pick
  // each core at the chosen stage, and the stage's deck menu. Mechanics text comes from the core sheet
  // (lib/rework/coreSheet.json); rolls from config.json `rework`; picks and decks from the ledger data bundle.
  import { onMount } from "svelte";
  import sheet from "../../lib/rework/coreSheet.json";
  import { roll } from "../../lib/rework/rules";
  import { STAGES, STAGE_LABEL, type ReworkConfig, type Stage, type StageBundle } from "../../lib/rework/types";

  interface Props {
    cfg: ReworkConfig;
    baseUrl: string;
  }
  let { cfg, baseUrl }: Props = $props();

  interface CoresPage {
    builds: number;
    core_use: Record<string, number>;
    core_use_by_group: Record<string, Record<string, number>>;
    group_builds: Record<string, number>;
    designs: { label: string; deck: string; mystery: boolean; implicit_keys: string[]; found_for: string; cores: string[];
               slots: [number, number][]; arcane_slots: [number, number][]; cells: [string, string][]; score: number;
               builds: number; mean_cycle: number | null; users: string[] }[];
    ranking: { label: string; cores: string[]; score: number }[];
  }

  interface PassGroup {
    n_decks: number;
    picks: Record<string, number>;
    gain: Record<string, number>;
    gain_rel: Record<string, number>;
    decks: { label: string; deck: string; keys: string[]; mystery: boolean; cores: string[]; score: number }[];
  }
  interface CorePass { stage: Stage; generated: string; groups: Record<string, PassGroup> }

  const GROUPS = ["Melee", "Caster", "Physical abilities", "Thorns & Block"];
  const PASS_GROUPS = ["Balanced", ...GROUPS, "Loot"];
  const X_DISPLAY = new Set(["empyreal", "arsenal", "pluto"]);
  const STALE = /don't yet grant/i;
  const GLYPH: Record<string, string> = { row: "R", col: "C", surr: "S", diag: "X", deluxe: "D", typeless: "T",
    dir_greed_up: "^", dir_greed_down: "v", dir_greed_left: "<", dir_greed_right: ">", arcane: "a", dead: "_" };

  let stage: Stage = $state("max");
  let pages: Partial<Record<Stage, CoresPage>> = $state.raw({});
  let passes: Partial<Record<Stage, CorePass | null>> = $state.raw({});
  let deckGroup = $state("Balanced");
  let meta = $state("");
  let error: string | null = $state(null);
  let qCores = $state("");
  let qMods = $state("");

  onMount(() => {
    try { stage = (localStorage.getItem("wvdo-cores-stage") as Stage) || stage; } catch { /* storage unavailable */ }
  });

  $effect(() => {
    const s = stage;
    try { localStorage.setItem("wvdo-cores-stage", s); } catch { /* storage unavailable */ }
    if (passes[s] === undefined) {
      fetch(`${baseUrl}rework/cores_pass_${s}.json`, { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : null))
        .then((p: CorePass | null) => {
          if (!p) console.error(`[cores] FALLBACK: no deck pass for ${s}; only the ledger builds' picks are shown`);
          passes = { ...passes, [s]: p };
        })
        .catch((e) => { console.error(`[cores] FALLBACK: deck pass for ${s} unreadable; ledger picks only`, e); passes = { ...passes, [s]: null }; });
    }
    if (pages[s]) return;
    fetch(`${baseUrl}rework/${s}.json`, { cache: "no-cache" })
      .then((r) => { if (!r.ok) throw new Error(`${s}.json: ${r.status}`); return r.json(); })
      .then((b: StageBundle & { cores_page?: CoresPage }) => {
        if (!b.cores_page) throw new Error(`the ${s} bundle has no cores page; re-export it (tools/site_bundle.py)`);
        pages = { ...pages, [s]: b.cores_page };
        meta = `ledger ${b.meta.ledger_commit || "?"}, results ${b.meta.results_generated}`;
      })
      .catch((e) => { error = e instanceof Error ? e.message : String(e); console.error("[cores] load failed", e); });
  });

  const page = $derived(pages[stage] ?? null);
  const pass = $derived(passes[stage] ?? null);
  const passGroups = $derived(pass ? PASS_GROUPS.filter((g) => pass.groups[g]) : []);

  function range(core: string, which: "normal" | "greater"): string {
    const r = cfg.rolls[core]?.[which];
    if (!r) return "–";
    const f = (v: number) => (X_DISPLAY.has(core) ? `×${+(1 + v).toFixed(3)}` : `${+v.toFixed(3)}`);
    return r[0] === r[1] ? f(r[0]) : `${f(r[0])}–${f(r[1]).replace("×", "")}`;
  }
  function stageRoll(core: string): string {
    if (!cfg.rolls[core]) return "–";
    const v = roll(cfg, core, stage);
    return X_DISPLAY.has(core) ? `×${+(1 + v).toFixed(3)}` : `${+v.toFixed(3)}`;
  }
  function share(core: string, group: string | null): { n: number; of: number } {
    if (!page) return { n: 0, of: 0 };
    if (!group) return { n: page.core_use[core] ?? 0, of: page.builds };
    return { n: page.core_use_by_group[group]?.[core] ?? 0, of: page.group_builds[group] ?? 0 };
  }
  function starsOf(f: number): number {
    return f >= 0.4 ? 5 : f >= 0.2 ? 4 : f >= 0.1 ? 3 : f > 0 ? 2 : 1;
  }
  function pickStars(core: string): number {
    if (pass?.groups.Balanced) return starsOf((pass.groups.Balanced.picks[core] ?? 0) / pass.groups.Balanced.n_decks);
    const s = share(core, null);
    return starsOf(s.of ? s.n / s.of : 0);
  }
  /** Power: the core's gain (with vs without, all builds) ranked against the other cores. */
  function powerStars(core: string): number | null {
    const g = pass?.groups.Balanced;
    if (!g || g.gain[core] === undefined) return null;
    const vals = Object.values(g.gain).sort((a, b) => a - b);
    const q = vals.indexOf(g.gain[core]) / Math.max(1, vals.length - 1);
    return 1 + Math.round(q * 4);
  }
  function passPick(core: string, g: string): { n: number; of: number } | null {
    const pg = pass?.groups[g];
    return pg ? { n: pg.picks[core] ?? 0, of: pg.n_decks } : null;
  }
  function gainText(core: string): string {
    const g = pass?.groups.Balanced;
    if (!g || g.gain[core] === undefined) return "–";
    const v = g.gain[core];
    return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
  }
  const coreRows = $derived(sheet.cores.filter((c) => !qCores || JSON.stringify(c).toLowerCase().includes(qCores.toLowerCase())));
  const modRows = $derived(sheet.mods.filter((m) => !qMods || JSON.stringify(m).toLowerCase().includes(qMods.toLowerCase())));

  /** The modifier's deck (or its Mystery single) in the deck pass ranking for one playstyle. */
  function modRank(m: { name: string; deck: string }, g: string): { rank: number; score: number; cores: string[]; of: number } | null {
    const pg = pass?.groups[g];
    if (!pg) return null;
    const key = m.name.toLowerCase();
    const i = pg.decks.findIndex((d) => (!d.mystery && d.label === m.deck) || (d.mystery && d.keys.length === 1 && d.keys[0] === key));
    return i < 0 ? null : { rank: i + 1, score: pg.decks[i].score, cores: pg.decks[i].cores, of: pg.decks.length };
  }
  const fmtScore = (g: string, v: number) => (g === "Loot" ? v.toLocaleString("en-US", { maximumFractionDigits: 0 }) : v.toFixed(2));

  function grid(d: CoresPage["designs"][number]) {
    const r0 = Math.min(...d.slots.map((s) => s[0])), c0 = Math.min(...d.slots.map((s) => s[1]));
    const nc = Math.max(...d.slots.map((s) => s[1])) - c0 + 1;
    return { r0, c0, nc, cells: d.slots.map((s, i) => ({ r: s[0] - r0, c: s[1] - c0, t: d.cells[i]?.[0] ?? "dead" })) };
  }
  const cap = (c: string) => (c === "color" ? "Colour" : c[0].toUpperCase() + c.slice(1));
  const cls = (t: string) => (["row", "col", "surr", "diag"].includes(t) ? "pos" : t === "deluxe" ? "dlx" : t === "typeless" ? "typ"
    : t.startsWith("dir_greed") ? "grd" : t === "arcane" ? "arc" : "emp");
</script>

<div class="sheet">
  <header class="top">
    <div class="eyebrow">Core rework · {meta || "loading the ledger data"}</div>
    <h1>Cores and deck modifiers</h1>
    <p class="lede">Every core and deck modifier with its reworked rolls and rules, how often the ledger's builds pick each
      core at the chosen stage, and that stage's deck menu.</p>
  </header>

  <div class="key">
    <div class="box">
      <h3>How a card's value is built</h3>
      <div class="formula">card × (1 + Σ additive) × Π multiplicative × (1 + Σ greed)</div>
      <p>Additive cores and modifiers sum. Multiplicative cores sum their own stacks inside (two empty slots on Void =
        ×1.16) and multiply each other. Greed bonuses on one card add; the Greed core adds to each when two or more point at it.</p>
    </div>
    <div class="box">
      <h3>Reading the numbers</h3>
      <ul>
        <li><b>Stage roll</b>: the value the optimizer uses at this stage (Early middle of normal, Mid top of normal, End and Max top of greater).</li>
        <li><b>Best set in</b>: how many decks put the core in their best core set, per playstyle (a full pass over every deck).</li>
        <li><b>Gain</b>: best set with the core minus best set without it, in cycles, averaged over the decks it is legal on.</li>
        <li><b>Power / pick stars</b> rank the all-builds gain and picks; <b>Ledger</b> counts the stage's ledger builds running the core.</li>
      </ul>
    </div>
    <div class="box">
      <h3>Stage</h3>
      <div class="seg" role="radiogroup" aria-label="Stage">
        {#each STAGES as s (s)}
          <button type="button" role="radio" aria-checked={stage === s} class="seg-btn" class:active={stage === s}
            onclick={() => (stage = s)}>{STAGE_LABEL[s]}</button>
        {/each}
      </div>
      <p class="dim">Greed cards +{Math.round(cfg.stages[stage].greed * 100)}% · {cfg.stages[stage].rolls.replace("_", " ")} rolls{cfg.stages[stage].mystery ? " · Mystery decks" : ""}{page ? ` · ${page.builds} builds` : ""}</p>
      {#if error}<p class="err">{error}</p>{/if}
    </div>
  </div>

  <section>
    <div class="sechead">
      <div><h2>Cores</h2><p>Rolls are the reworked lesser / normal / greater values.</p></div>
      <input type="search" bind:value={qCores} placeholder="Filter cores" aria-label="Filter cores" />
    </div>
    <div class="tbl"><table>
      <thead>
        <tr><th rowspan="2">Core</th><th rowspan="2">Mechanic</th><th rowspan="2">Lesser</th><th rowspan="2">Normal</th>
          <th rowspan="2">Greater</th><th rowspan="2">Stage roll</th><th rowspan="2">Stacking</th><th rowspan="2">Power</th><th rowspan="2">Pick</th>
          {#if passGroups.length}<th colspan={passGroups.length} class="grp">Best set in (of {pass?.groups.Balanced?.n_decks ?? "?"} decks)</th>{/if}
          <th rowspan="2" class="c">Gain</th><th rowspan="2" class="c">Ledger</th><th rowspan="2">Notes</th></tr>
        <tr>{#each passGroups as g (g)}<th class="c">{g === "Balanced" ? "All" : g}</th>{/each}</tr>
      </thead>
      <tbody>
        {#each coreRows as c (c.name)}
          {@const all = share(c.key, null)}
          {@const pw = powerStars(c.key)}
          <tr>
            <td><div class="nm">{c.name}{#if c.change}<span class="chg {c.change}">{c.change}</span>{/if}</div><div class="id">{c.kind}</div></td>
            <td class="mech">{c.mechanic}</td>
            <td class="num">{c.lesser}</td>
            <td class="num">{range(c.key, "normal")}</td>
            <td class="num">{range(c.key, "greater")}</td>
            <td class="num hi">{stageRoll(c.key)}</td>
            <td><span class="pill {c.stacking.startsWith('Mult') ? 'mul' : 'add'}">{c.stacking}</span></td>
            <td>{#if pw !== null}<span class="stars" aria-label="{pw} of 5">{"★".repeat(pw)}{"☆".repeat(5 - pw)}</span>{:else}<span class="of">–</span>{/if}</td>
            <td><span class="stars" aria-label="{pickStars(c.key)} of 5">{"★".repeat(pickStars(c.key))}{"☆".repeat(5 - pickStars(c.key))}</span></td>
            {#each passGroups as g (g)}
              {@const pp = passPick(c.key, g)}
              <td class="num c">{pp ? pp.n : "–"}</td>
            {/each}
            <td class="num c">{gainText(c.key)}</td>
            <td class="num c">{all.n}<span class="of">/{all.of}</span></td>
            <td class="note">{STALE.test(c.note) ? "" : c.note}</td>
          </tr>
        {/each}
        {#each sheet.other as o (o.name)}
          <tr class="other"><td><div class="nm">{o.name}</div></td><td colspan="2">{o.kind}</td><td colspan={10 + passGroups.length} class="note">{o.note}</td></tr>
        {/each}
      </tbody>
    </table></div>
    <p class="dim">Nitwit and Archive need a deck with Arcane slots. Construction and Arcane cores are not searched; the optimizer lets you place their slots by hand.</p>
  </section>

  {#if pass}
    <section>
      <div class="sechead">
        <div><h2>Best decks</h2><p>Top decks per playstyle from the full deck pass, with the cores the search chose.</p></div>
        <div class="seg small-seg" role="radiogroup" aria-label="Playstyle">
          {#each passGroups as g (g)}
            <button type="button" role="radio" aria-checked={deckGroup === g} class="seg-btn" class:active={deckGroup === g}
              onclick={() => (deckGroup = g)}>{g === "Balanced" ? "All builds" : g}</button>
          {/each}
        </div>
      </div>
      <div class="decks">
        {#each (pass.groups[deckGroup] ?? pass.groups.Balanced).decks.slice(0, 6) as d, i (d.label)}
          <article class="deck">
            <header><span class="rank">#{i + 1}</span><span class="score">{fmtScore(deckGroup, d.score)}</span></header>
            <h4 title={d.label}>{d.label}</h4>
            <div class="cores">{d.cores.map(cap).join(" + ") || "no cores"}</div>
          </article>
        {/each}
      </div>
      <p class="dim">Scores are cycles averaged over the playstyle's ledger builds (Loot: omega + 5 × mythic rolls per 1000 chests). Deck pass {pass.generated}.</p>
    </section>
  {/if}

  <section>
    <div class="sechead">
      <div><h2>{STAGE_LABEL[stage]} deck menu</h2><p>The designs the ledger's builds choose from at this stage, with the builds that took each.</p></div>
    </div>
    <div class="legend">
      <span class="swatch"><span class="g pos">R</span>Positional</span><span class="swatch"><span class="g dlx">D</span>Deluxe</span>
      <span class="swatch"><span class="g typ">T</span>Typeless / feeder</span><span class="swatch"><span class="g grd">^</span>Greed</span>
      <span class="swatch"><span class="g arc">a</span>Arcane</span><span class="swatch"><span class="g emp">_</span>Empty</span>
    </div>
    <div class="decks">
      {#each page?.designs ?? [] as d, i (i)}
        {@const gr = grid(d)}
        <article class="deck">
          <header><span class="rank">#{i + 1}</span><span class="score">{d.builds} build{d.builds === 1 ? "" : "s"}</span></header>
          <h4>{d.deck}</h4>
          {#if d.implicit_keys.length}<div class="sub">{d.implicit_keys.join(" + ")}{d.mystery ? " at 75%" : ""}</div>{/if}
          <div class="cores">{d.cores.map(cap).join(" + ") || "no cores"}</div>
          <div class="mgrid" style:grid-template-columns="repeat({gr.nc}, 20px)">
            {#each gr.cells as cell, j (j)}
              <span class="g {cls(cell.t)}" style:grid-row={cell.r + 1} style:grid-column={cell.c + 1}>{GLYPH[cell.t] ?? "?"}</span>
            {/each}
          </div>
          <div class="comp">Mean cycle {d.mean_cycle !== null ? d.mean_cycle.toFixed(2) : "–"} · DeckFAST value {d.score.toFixed(2)} · found for {d.found_for === "all" ? "all builds" : `group ${d.found_for}`}</div>
          {#if d.users.length}<div class="comp">{d.users.join(", ")}</div>{/if}
        </article>
      {/each}
    </div>
  </section>

  <section>
    <div class="sechead">
      <div><h2>Deck modifiers</h2><p>Modifiers don't roll or use a socket. Rank is the deck's place in the full deck pass per playstyle (a Mystery modifier ranks as the Mystery deck with that modifier alone).</p></div>
      <input type="search" bind:value={qMods} placeholder="Filter modifiers" aria-label="Filter modifiers" />
    </div>
    <div class="tbl"><table class="mods">
      <thead><tr><th rowspan="2">Modifier</th><th rowspan="2">Mechanic</th><th rowspan="2">Value</th><th rowspan="2">Stacking</th>
        {#if passGroups.length}<th colspan={passGroups.length} class="grp">Deck rank</th>{/if}<th rowspan="2">Best (all builds)</th></tr>
        <tr>{#each passGroups as g (g)}<th class="c">{g === "Balanced" ? "All" : g}</th>{/each}</tr></thead>
      <tbody>
        {#each modRows as m (m.name)}
          {@const r = modRank(m, "Balanced")}
          <tr>
            <td><div class="nm">{m.name}</div><div class="id">{m.deck}</div></td>
            <td class="mech">{m.mechanic}</td>
            <td class="num hi">{m.value}</td>
            <td><span class="pill frq">{m.stacking}</span></td>
            {#each passGroups as g (g)}
              {@const rg = modRank(m, g)}
              <td class="num c">{rg ? `#${rg.rank}` : "–"}</td>
            {/each}
            <td class="note">{r ? `${r.score.toFixed(2)} · ${r.cores.map(cap).join(" + ") || "no cores"}` : ""}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
  </section>
</div>

<style>
  .sheet { max-width: 1500px; margin: 0 auto; padding: 20px 16px 48px; display: grid; gap: 22px; color: var(--text-primary); }
  .top { display: grid; gap: 6px; max-width: 80ch; }
  .eyebrow { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
  h1 { margin: 0; font-size: 28px; }
  h2 { margin: 0; font-size: 18px; }
  .lede, .dim { color: var(--text-secondary); margin: 0; }
  .dim { font-size: 12px; }
  .err { color: #FCA5A5; font-size: 12px; margin: 0; }
  .key { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
  .box { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; display: grid; gap: 8px; align-content: start; min-width: 0; }
  .box h3 { margin: 0; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-secondary); }
  .box p { margin: 0; font-size: 13px; color: var(--text-secondary); }
  .box ul { margin: 0; padding-left: 18px; font-size: 13px; color: var(--text-secondary); display: grid; gap: 3px; }
  .formula { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; background: rgba(99,102,241,.15); color: #C7D2FE; padding: 8px 10px; border-radius: 4px; overflow-x: auto; }
  .seg { display: flex; background: var(--bg-input); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .seg-btn { flex: 1; padding: 5px 0; font-size: 12px; background: transparent; color: var(--text-secondary); border: 0; cursor: pointer; font-family: inherit; }
  .seg-btn + .seg-btn { border-left: 1px solid var(--border); }
  .seg-btn.active { background: var(--accent); color: #fff; font-weight: 600; }
  .small-seg { max-width: 640px; flex: 1 1 420px; }
  .small-seg .seg-btn { padding: 5px 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  section { display: grid; gap: 10px; }
  .sechead { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 12px; }
  .sechead p { margin: 2px 0 0; color: var(--text-secondary); font-size: 13px; }
  input[type="search"] { font: inherit; font-size: 13px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
                         background: var(--bg-input); color: var(--text-primary); width: 220px; max-width: 100%; }
  .tbl { overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-card); }
  table { border-collapse: collapse; width: 100%; min-width: 1300px; font-size: 13px; }
  table.mods { min-width: 900px; }
  th { position: sticky; top: 0; background: var(--bg-card); text-align: left; font-size: 11px; font-weight: 600; letter-spacing: .05em;
       text-transform: uppercase; color: var(--text-muted); padding: 8px 10px; border-bottom: 2px solid var(--border); white-space: nowrap; }
  th.grp { text-align: center; border-bottom: 1px solid var(--border); }
  th.c, td.c { text-align: center; }
  td { padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:last-child td { border-bottom: 0; }
  tr.other td { color: var(--text-secondary); }
  .nm { font-weight: 600; }
  .id { font-size: 11.5px; color: var(--text-muted); }
  .mech { min-width: 220px; max-width: 320px; color: var(--text-secondary); }
  .num { font-variant-numeric: tabular-nums; white-space: nowrap; }
  .num.hi { color: #A5B4FC; font-weight: 600; }
  .of { color: var(--text-muted); font-size: 11px; }
  .note { min-width: 200px; max-width: 300px; font-size: 12.5px; color: var(--text-secondary); }
  .pill { display: inline-block; font-size: 11px; padding: 2px 7px; border-radius: 3px; white-space: nowrap; }
  .pill.add { color: #86EFAC; background: rgba(34,197,94,.15); }
  .pill.mul { color: #FCA5A5; background: rgba(239,68,68,.15); }
  .pill.frq { color: #93C5FD; background: rgba(59,130,246,.15); }
  .chg { font-size: 10px; font-weight: 500; padding: 1px 5px; border-radius: 3px; margin-left: 6px; text-transform: uppercase; letter-spacing: .04em; vertical-align: 1px; }
  .chg.nerfed { color: #FCA5A5; background: rgba(239,68,68,.15); }
  .chg.buffed, .chg.new { color: #86EFAC; background: rgba(34,197,94,.15); }
  .chg.reworked { color: #C4B5FD; background: rgba(139,92,246,.18); }
  .stars { color: #FBBF24; letter-spacing: 1px; white-space: nowrap; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--text-secondary); align-items: center; }
  .swatch { display: inline-flex; align-items: center; gap: 5px; }
  .decks { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
  .deck { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 12px; display: grid; gap: 6px; align-content: start; min-width: 0; }
  .deck header { display: flex; justify-content: space-between; align-items: baseline; }
  .rank { color: var(--text-muted); font-size: 12px; }
  .score { color: #A5B4FC; font-weight: 600; font-variant-numeric: tabular-nums; }
  .deck h4 { margin: 0; font-size: 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { font-size: 12px; color: var(--text-secondary); }
  .cores { font-size: 13px; font-weight: 600; }
  .comp { font-size: 12px; color: var(--text-muted); }
  .mgrid { display: grid; gap: 2px; overflow-x: auto; padding: 4px 0; }
  .g { width: 20px; height: 20px; display: grid; place-items: center; font-size: 11px; font-family: ui-monospace, Consolas, monospace;
       border-radius: 3px; color: #0F172A; }
  .g.pos { background: #A9CCE3; } .g.dlx { background: #D7BDE2; } .g.typ { background: #A8D5A2; }
  .g.grd { background: #F9E79F; } .g.arc { background: #C4B5FD; } .g.emp { background: #334155; color: var(--text-muted); }
</style>
