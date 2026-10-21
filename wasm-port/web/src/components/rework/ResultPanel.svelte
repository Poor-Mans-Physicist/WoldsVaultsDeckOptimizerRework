<script lang="ts">
  // Right column: what the run picked (cards, cores) and what the deck gives (stat totals, granted levels, loot).
  import { TYPE_LABEL } from "../../lib/palette";
  import type { CardType } from "../../lib/types";
  import { MYTHIC_WEIGHT } from "../../lib/rework/loot";
  import { formatStat, statLabel, type ResultView } from "../../lib/rework/results";

  interface Props {
    view: ResultView | null;
    /** Display name of an implicit key (the catalog's name). */
    modName?: (key: string) => string;
  }
  let { view, modName = (k) => k }: Props = $props();

  const MULT_CORES = new Set(["void", "empyreal", "arsenal", "aegis", "tool", "pluto"]);
  const CORE_NAME: Record<string, string> = { color: "Colour", greed: "Greed core" };

  function coreText(c: [string, number, number, string]): string {
    const [name, v, k, col] = c;
    const label = CORE_NAME[name] ?? name[0].toUpperCase() + name.slice(1);
    const val = MULT_CORES.has(name) ? `×${(1 + v).toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}`
                                     : `+${(v * 100).toFixed(1).replace(/\.0$/, "")}%`;
    return `${label}${col ? ` (${col})` : ""} ${val}${k ? ` on ${k} slots` : ""}`;
  }

  const scoreLine = $derived.by(() => {
    if (!view) return "";
    const k = view.objective.kind;
    if (k === "loot") return "omega (green) + 5 × mythic (purple) rolls per 1000 chests";
    if (k === "offense") return "average damage cycles";
    if (k === "defense") return "average survival cycles";
    if (k === "ndm") return "NDM";
    return "average cycles, min(damage, survival)";
  });

  const cards = $derived.by(() => {
    if (!view) return [];
    const m = new Map<string, { label: string; sub: string; n: number; total: number; attr: string | null }>();
    for (const s of view.slots) {
      const id = s.ident;
      if (!id || s.t === "dead") continue;
      let key: string, label: string, sub: string;
      if (id.grant) {
        key = `g|${id.id}`; label = id.grant[1].replace(/_/g, " "); sub = `${id.kind === "arcane" ? "Arcane" : "Knack"} +${id.grant[2]} lv`;
      } else if (id.attr) {
        key = `s|${id.attr}|${s.t}`; label = statLabel(id.attr); sub = TYPE_LABEL[s.t as CardType] ?? s.t;
      } else if (id.kind === "greed") {
        key = "greed"; label = "Greed cards"; sub = "";
      } else {
        key = `o|${id.kind}|${id.id}`; label = id.kind === "battery" ? `${id.id} card` : id.kind; sub = "";
      }
      const r = m.get(key) ?? { label, sub, n: 0, total: 0, attr: id.attr ?? null };
      r.n++; r.total += s.contrib ?? 0;
      m.set(key, r);
    }
    return [...m.values()].sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
  });

  function num(v: number, d = 2): string {
    return v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  }
</script>

<div class="panel">
  {#if !view}
    <div class="placeholder">Run the optimizer to see the cards it picks and the deck's total stats here.</div>
  {:else}
    <section>
      {#if view.objective.kind === "loot" && view.loot}
        <div class="score">{num(view.score, 0)}
          <span class="split">(<span class="omega" title="{num(view.loot.omega, 0)} omega rolls per 1000 chests">{num(view.loot.omega, 0)}</span>
            + <span class="mythic" title="5 × {num(view.loot.mythic, 1)} mythic rolls per 1000 chests">{num(MYTHIC_WEIGHT * view.loot.mythic, 0)}</span>)</span></div>
      {:else}
        <div class="score">{num(view.score, 3)}</div>
      {/if}
      <div class="sub">{scoreLine}</div>
      <div class="sub">{view.objective.label}</div>
    </section>

    <section>
      <h3>Deck</h3>
      <div class="line">{view.deckName}</div>
      {#if view.implicits.length}
        <div class="line muted">{view.implicits.map(modName).join(" + ")}</div>
      {/if}
      <div class="chips">
        {#each view.cores as c (c[0])}
          <span class="chip">{coreText(c)}</span>
        {:else}
          <span class="muted">No cores</span>
        {/each}
      </div>
    </section>

    {#if view.loot}
      <section>
        <h3>Loot</h3>
        <div class="kv"><span>Item Quantity (with base)</span><span>{formatStat("x", view.loot.iq)}</span></div>
        <div class="kv"><span>Item Rarity (with base)</span><span>{formatStat("x", view.loot.ir)}</span></div>
        {#each view.loot.paths as p (p.label)}
          <div class="kv" class:best={p.label === view.loot.path}><span>{p.label}</span>
            <span>{num(p.score, 0)} <span class="psplit">(<span class="omega">{num(p.omega, 0)}</span> + <span class="mythic">{num(MYTHIC_WEIGHT * p.mythic, 0)}</span>)</span></span></div>
        {/each}
      </section>
    {/if}

    <section>
      <h3>Deck stats</h3>
      {#each view.totals as t (t.attr)}
        <div class="kv"><span>{t.label} <em>({t.cards})</em></span><span>{formatStat(t.attr, t.value)}</span></div>
      {:else}
        <div class="muted">No stat cards.</div>
      {/each}
      {#if view.levels.length}
        <h4>Granted levels</h4>
        {#each view.levels as l (l.kind + l.key)}
          <div class="kv"><span>{l.key.replace(/_/g, " ")} <em>({l.kind})</em></span><span>+{l.levels}</span></div>
        {/each}
      {/if}
    </section>

    <section>
      <h3>Cards</h3>
      {#each cards as c (c.label + c.sub)}
        <div class="card-row">
          <span class="n">{c.n}×</span>
          <span class="name">{c.label}{#if c.sub}<em> {c.sub}</em>{/if}</span>
          <span class="val">{c.attr ? formatStat(c.attr, c.total) : ""}</span>
        </div>
      {/each}
    </section>
  {/if}
</div>

<style>
  .panel { display: flex; flex-direction: column; gap: 14px; font-size: 13px; font-variant-numeric: tabular-nums; }
  .placeholder { color: var(--text-muted); padding: 12px 0; }
  section { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; min-width: 0; }
  h3 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--text-secondary); }
  h4 { margin: 10px 0 4px; font-size: 12px; color: var(--text-secondary); }
  .score { font-size: 28px; font-weight: 700; line-height: 1.1; }
  .split { font-size: 15px; font-weight: 600; color: var(--text-secondary); white-space: nowrap; }
  .psplit { font-size: 11px; color: var(--text-muted); white-space: nowrap; }
  .omega { color: #4ADE80; }
  .mythic { color: #C084FC; }
  .sub { color: var(--text-secondary); font-size: 12px; }
  .line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .muted { color: var(--text-muted); }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
  .chip { background: var(--bg-input); border-radius: 10px; padding: 2px 8px; font-size: 12px; max-width: 100%;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .kv { display: flex; justify-content: space-between; gap: 8px; padding: 1px 0; }
  .kv span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .kv span:last-child { flex: none; }
  .kv.best span { color: var(--accent); font-weight: 600; }
  em { color: var(--text-muted); font-style: normal; }
  .card-row { display: grid; grid-template-columns: 2.4em 1fr auto; gap: 6px; padding: 1px 0; }
  .card-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .card-row .n { color: var(--text-secondary); }
</style>
