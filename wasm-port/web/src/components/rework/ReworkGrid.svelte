<script lang="ts">
  // The deck as a fixed grid of equal cells: card type, card colour, the stat it carries and its value after the
  // deck's multipliers. Hovering a card shows its breakdown in a fixed-width popup (no layout shift).
  import type { Deck } from "../../lib/deck";
  import { slotBg, COLOR_HEX, TYPE_GLYPH, ARCANE_BORDER } from "../../lib/palette";
  import type { CardType, Color } from "../../lib/types";
  import { formatStat, sourceLabel, statLabel, type SlotView } from "../../lib/rework/results";

  interface Props {
    deck: Deck;
    slots: SlotView[] | null;
  }
  let { deck, slots }: Props = $props();

  const CELL = 58;
  const GAP = 4;

  const SHORT: Record<string, string> = {
    "Ability Power": "AP", "Area of Effect": "AOE", "Armor": "ARM", "Attack Speed": "AS", "Cooldown Reduction": "CDR",
    "Crit Mitigation": "CRIT", "Damage": "DMG", "Healing Efficiency": "HEAL", "Health": "HP", "Item Quantity": "IQ",
    "Item Rarity": "IR", "Knockback Resistance": "KB", "Mana": "MANA", "Mana %": "MP%", "Mana Regen": "REGN",
    "Resistance": "RES", "Thorns": "THRN",
  };

  const bounds = $derived.by(() => {
    const rows = deck.slots.map((s) => s[0]), cols = deck.slots.map((s) => s[1]);
    return { r0: Math.min(...rows), c0: Math.min(...cols), nr: Math.max(...rows) - Math.min(...rows) + 1,
             nc: Math.max(...cols) - Math.min(...cols) + 1 };
  });

  const cells = $derived.by(() => {
    const arc = new Set(deck.arcaneSlotIndices);
    return deck.slots.map((s, i) => ({ i, r: s[0] - bounds.r0, c: s[1] - bounds.c0, arcane: arc.has(i),
                                       view: slots ? slots[i] : null }));
  });

  function cardLine(v: SlotView | null): { top: string; bottom: string } {
    if (!v || v.t === "dead") return { top: "", bottom: "" };
    const id = v.ident;
    if (id?.grant) {
      const key = id.grant[1].replace(/_/g, " ");
      return { top: key.length > 7 ? key.slice(0, 6) + "." : key, bottom: `+${id.grant[2]} lv` };
    }
    if (id?.attr) {
      const lab = statLabel(id.attr);
      return { top: SHORT[lab] ?? lab.slice(0, 4).toUpperCase(), bottom: v.contrib !== null ? formatStat(id.attr, v.contrib) : "" };
    }
    if (id?.kind === "battery") return { top: id.id.slice(0, 5), bottom: "" };
    return { top: "", bottom: "" };
  }

  let hover: number | null = $state(null);
  const hv = $derived(hover !== null && slots ? slots[hover] : null);
  const hoverPos = $derived.by(() => {
    if (hover === null) return null;
    const c = cells[hover];
    const x = c.c * (CELL + GAP);
    const right = x + 260 > bounds.nc * (CELL + GAP);
    return { left: right ? Math.max(0, x - 260 + CELL) : x, top: (c.r + 1) * (CELL + GAP) };
  });

  function fmt(n: number, d = 3): string {
    return n.toLocaleString("en-US", { maximumFractionDigits: d });
  }
</script>

<div class="grid-wrap" style:width="{bounds.nc * (CELL + GAP)}px" style:height="{bounds.nr * (CELL + GAP)}px">
  {#each cells as cell (cell.i)}
    {@const v = cell.view}
    {@const line = cardLine(v)}
    <div
      class="cell"
      class:arcane={cell.arcane}
      class:empty={!v}
      style:left="{cell.c * (CELL + GAP)}px"
      style:top="{cell.r * (CELL + GAP)}px"
      style:width="{CELL}px"
      style:height="{CELL}px"
      style:background={v ? slotBg(v.t as CardType) : "var(--bg-empty-slot)"}
      style:border-color={cell.arcane ? ARCANE_BORDER : "transparent"}
      role="img"
      aria-label={v ? `${v.t} ${line.top} ${line.bottom}` : "empty slot"}
      onmouseenter={() => (hover = cell.i)}
      onmouseleave={() => (hover = null)}
    >
      {#if v && v.t !== "dead"}
        {#if v.color && v.t !== "arcane"}
          <span class="cbar" style:background={COLOR_HEX[v.color as Color] ?? "transparent"}></span>
        {/if}
        <span class="glyph">{TYPE_GLYPH[v.t as CardType] ?? "?"}</span>
        <span class="top">{line.top}</span>
        <span class="bottom">{line.bottom}</span>
      {:else if v}
        <span class="glyph dead">_</span>
      {/if}
    </div>
  {/each}

  {#if hv && hoverPos && hv.t !== "dead"}
    <div class="pop" style:left="{hoverPos.left}px" style:top="{hoverPos.top}px">
      {#if hv.ident?.grant}
        <div class="pop-title">{hv.ident.id}</div>
        <div>+{hv.ident.grant[2]} {hv.ident.grant[0]} level{hv.ident.grant[2] === 1 ? "" : "s"}: {hv.ident.grant[1].replace(/_/g, " ")}</div>
        <div class="muted">Level cards are never scaled by deck multipliers.</div>
      {:else if hv.ident?.attr && hv.parts}
        <div class="pop-title">{statLabel(hv.ident.attr)} {hv.contrib !== null ? formatStat(hv.ident.attr, hv.contrib) : ""}</div>
        <div class="row"><span>card value</span><span>{fmt(hv.ident.value ?? 0, 4)}</span></div>
        <div class="row"><span>base ({hv.t})</span><span>{fmt(hv.parts.base)}</span></div>
        {#each hv.parts.adds as [src, v] (src)}
          <div class="row"><span>+ {sourceLabel(src)}</span><span>{fmt(v)}</span></div>
        {/each}
        {#each hv.parts.mults as [src, f] (src)}
          <div class="row"><span>× {sourceLabel(src)}</span><span>{fmt(f)}</span></div>
        {/each}
        {#if hv.parts.boost !== 1}
          <div class="row"><span>× greed</span><span>{fmt(hv.parts.boost)}</span></div>
        {/if}
        {#if hv.parts.empyreal !== 1}
          <div class="row"><span>× Empyreal</span><span>{fmt(hv.parts.empyreal)}</span></div>
        {/if}
        <div class="row total"><span>multiplier</span><span>{fmt(hv.term)}</span></div>
      {:else if hv.ident?.kind === "greed"}
        <div class="pop-title">Greed card</div>
        <div class="muted">Boosts the card it points at.</div>
      {:else if hv.ident}
        <div class="pop-title">{hv.ident.kind === "battery" ? `${hv.ident.id} card` : hv.ident.kind}</div>
        <div class="muted">No stat of its own; feeds the cores and modifiers that count it.</div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .grid-wrap { position: relative; margin: 0 auto; }
  .cell {
    position: absolute; box-sizing: border-box; border: 2px solid transparent; border-radius: 6px;
    color: #111827; overflow: hidden; font-variant-numeric: tabular-nums;
  }
  .cell.empty { opacity: 0.6; }
  .cbar { position: absolute; left: 0; top: 0; bottom: 0; width: 5px; }
  .glyph { position: absolute; top: 2px; right: 4px; font-size: 11px; font-weight: 700; opacity: 0.7; }
  .glyph.dead { left: 0; right: 0; text-align: center; top: 18px; opacity: 0.4; }
  .top {
    position: absolute; left: 8px; right: 4px; top: 16px; font-size: 11px; font-weight: 700;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .bottom {
    position: absolute; left: 8px; right: 3px; bottom: 4px; font-size: 10px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .pop {
    position: absolute; z-index: 5; width: 260px; box-sizing: border-box; padding: 8px 10px;
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; font-size: 12px;
    color: var(--text-primary); box-shadow: 0 6px 18px rgba(0,0,0,.4); font-variant-numeric: tabular-nums;
    pointer-events: none;
  }
  .pop-title { font-weight: 600; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row { display: flex; justify-content: space-between; gap: 8px; }
  .row span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }
  .row.total { border-top: 1px solid var(--border); margin-top: 3px; padding-top: 3px; font-weight: 600; }
  .muted { color: var(--text-muted); }
</style>
