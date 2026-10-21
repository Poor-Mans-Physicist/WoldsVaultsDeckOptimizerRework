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

  const GROUPS = ["Melee", "Caster", "Physical abilities", "Thorns & Block"];
  const X_DISPLAY = new Set(["empyreal", "arsenal", "pluto"]);
  const STALE = /don't yet grant/i;
  const GLYPH: Record<string, string> = { row: "R", col: "C", surr: "S", diag: "X", deluxe: "D", typeless: "T",
    dir_greed_up: "^", dir_greed_down: "v", dir_greed_left: "<", dir_greed_right: ">", arcane: "a", dead: "_" };

  let stage: Stage = $state("max");
  let pages: Partial<Record<Stage, CoresPage>> = $state.raw({});
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
  function stars(core: string): number {
    const s = share(core, null);
    const f = s.of ? s.n / s.of : 0;
    return f >= 0.4 ? 5 : f >= 0.2 ? 4 : f >= 0.1 ? 3 : f > 0 ? 2 : 1;
  }
  const coreRows = $derived(sheet.cores.filter((c) => !qCores || JSON.stringify(c).toLowerCase().includes(qCores.toLowerCase())));
  const modRows = $derived(sheet.mods.filter((m) => !qMods || JSON.stringify(m).toLowerCase().includes(qMods.toLowerCase())));

  function modRank(m: { name: string; deck: string }): { rank: number; score: number; cores: string[] } | null {
    if (!page) return null;
    const key = m.name.toLowerCase();
    const i = page.ranking.findIndex((r) => r.label === m.deck || (r.label.startsWith("The Mystery Deck") && r.label.toLowerCase().includes(key)));
    return i < 0 ? null : { rank: i + 1, score: page.ranking[i].score, cores: page.ranking[i].cores };
  }

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
        <li><b>Picked</b>: how many of the stage's ledger builds run the core, overall and per playstyle.</li>
        <li><b>Stars</b> summarise the overall pick share.</li>
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
          <th rowspan="2">Greater</th><th rowspan="2">Stage roll</th><th rowspan="2">Stacking</th><th rowspan="2">Pick</th>
          <th colspan={1 + GROUPS.length} class="grp">Picked by {STAGE_LABEL[stage]} builds</th><th rowspan="2">Notes</th></tr>
        <tr><th class="c">All</th>{#each GROUPS as g (g)}<th class="c">{g}</th>{/each}</tr>
      </thead>
      <tbody>
        {#each coreRows as c (c.name)}
          {@const all = share(c.key, null)}
          <tr>
            <td><div class="nm">{c.name}{#if c.change}<span class="chg {c.change}">{c.change}</span>{/if}</div><div class="id">{c.kind}</div></td>
            <td class="mech">{c.mechanic}</td>
            <td class="num">{c.lesser}</td>
            <td class="num">{range(c.key, "normal")}</td>
            <td class="num">{range(c.key, "greater")}</td>
            <td class="num hi">{stageRoll(c.key)}</td>
            <td><span class="pill {c.stacking.startsWith('Mult') ? 'mul' : 'add'}">{c.stacking}</span></td>
            <td><span class="stars" aria-label="{stars(c.key)} of 5">{"★".repeat(stars(c.key))}{"☆".repeat(5 - stars(c.key))}</span></td>
            <td class="num c">{all.n}<span class="of">/{all.of}</span></td>
            {#each GROUPS as g (g)}
              {@const s = share(c.key, g)}
              <td class="num c">{s.n}<span class="of">/{s.of}</span></td>
            {/each}
            <td class="note">{STALE.test(c.note) ? "" : c.note}</td>
          </tr>
        {/each}
        {#each sheet.other as o (o.name)}
          <tr class="other"><td><div class="nm">{o.name}</div></td><td colspan="2">{o.kind}</td><td colspan={10 + GROUPS.length} class="note">{o.note}</td></tr>
        {/each}
      </tbody>
    </table></div>
    <p class="dim">Nitwit and Archive need a deck with Arcane slots. Construction and Arcane cores are not searched; the optimizer lets you place their slots by hand.</p>
  </section>

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
      <div><h2>Deck modifiers</h2><p>Modifiers don't roll or use a socket. Rank is the deck's place in DeckFAST's all-builds ranking at this stage (top 40).</p></div>
      <input type="search" bind:value={qMods} placeholder="Filter modifiers" aria-label="Filter modifiers" />
    </div>
    <div class="tbl"><table class="mods">
      <thead><tr><th>Modifier</th><th>Mechanic</th><th>Value</th><th>Stacking</th><th class="c">Rank</th><th>Best (all builds)</th></tr></thead>
      <tbody>
        {#each modRows as m (m.name)}
          {@const r = modRank(m)}
          <tr>
            <td><div class="nm">{m.name}</div><div class="id">{m.deck}</div></td>
            <td class="mech">{m.mechanic}</td>
            <td class="num hi">{m.value}</td>
            <td><span class="pill frq">{m.stacking}</span></td>
            <td class="num c">{r ? `#${r.rank}` : "–"}</td>
            <td class="note">{r ? `${r.score.toFixed(2)} · ${r.cores.map(cap).join(" + ")}` : ""}</td>
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
