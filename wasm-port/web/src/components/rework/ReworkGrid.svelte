<script lang="ts">
  // The deck as a fixed grid of equal cells: card type, card colour, the stat it carries and its value after the
  // deck's multipliers. Hovering a card shows its breakdown in a fixed-width popup (no layout shift). With the
  // structural cores on, "+" tiles add Construction slots (right-click an added slot to remove it) and clicking a
  // regular slot converts it to arcane (right-click to revert).
  import type { Deck } from "../../lib/deck";
  import { slotBg, COLOR_HEX, TYPE_GLYPH, ARCANE_BORDER } from "../../lib/palette";
  import type { CardType, Color, Position } from "../../lib/types";
  import { formatStat, sourceLabel, statLabel, type SlotView } from "../../lib/rework/results";
  import { cardImages, type CardArt } from "../../lib/rework/cardArt";

  interface Props {
    deck: Deck;
    slots: SlotView[] | null;
    /** Card textures (cardArt.ts); empty draws plain tiles. */
    art?: CardArt;
    tier?: number;
    candidates?: Position[];
    added?: Position[];
    converted?: Position[];
    canConvert?: boolean;
    onPlace?: (p: Position) => void;
    onRemove?: (p: Position) => void;
    onConvert?: (p: Position) => void;
    onUnconvert?: (p: Position) => void;
  }
  let { deck, slots, art = {}, tier = 5, candidates = [], added = [], converted = [], canConvert = false,
        onPlace, onRemove, onConvert, onUnconvert }: Props = $props();

  const CELL = 58;
  const GAP = 4;
  const key = (p: readonly [number, number]) => `${p[0]},${p[1]}`;

  const SHORT: Record<string, string> = {
    "Ability Power": "AP", "Area of Effect": "AOE", "Armor": "ARM", "Attack Speed": "AS", "Cooldown Reduction": "CDR",
    "Crit Mitigation": "CRIT", "Damage": "DMG", "Healing Efficiency": "HEAL", "Health": "HP", "Item Quantity": "IQ",
    "Item Rarity": "IR", "Knockback Resistance": "KB", "Mana": "MANA", "Mana %": "MP%", "Mana Regen": "REGN",
    "Resistance": "RES", "Thorns": "THRN",
  };

  const bounds = $derived.by(() => {
    const all = [...deck.slots, ...candidates];
    const rows = all.map((s) => s[0]), cols = all.map((s) => s[1]);
    return { r0: Math.min(...rows), c0: Math.min(...cols), nr: Math.max(...rows) - Math.min(...rows) + 1,
             nc: Math.max(...cols) - Math.min(...cols) + 1 };
  });

  const addedSet = $derived(new Set(added.map(key)));
  const convertedSet = $derived(new Set(converted.map(key)));

  const cells = $derived.by(() => {
    const arc = new Set(deck.arcaneSlotIndices);
    return deck.slots.map((s, i) => ({ i, pos: s, r: s[0] - bounds.r0, c: s[1] - bounds.c0, arcane: arc.has(i),
                                       added: addedSet.has(key(s)), converted: convertedSet.has(key(s)),
                                       view: slots ? slots[i] : null }));
  });

  function cardLine(v: SlotView | null): { top: string; bottom: string } {
    if (!v || v.t === "dead") return { top: "", bottom: "" };
    const id = v.ident;
    if (id?.grant) {
      const k = id.grant[1].replace(/_/g, " ");
      return { top: k.length > 7 ? k.slice(0, 6) + "." : k, bottom: `+${id.grant[2]} lv` };
    }
    if (id?.attr) {
      const lab = statLabel(id.attr);
      return { top: SHORT[lab] ?? lab.slice(0, 4).toUpperCase(), bottom: v.contrib !== null ? formatStat(id.attr, v.contrib) : "" };
    }
    if (id?.kind === "battery") return { top: id.id.slice(0, 5), bottom: "" };
    return { top: "", bottom: "" };
  }

  const ARROW: Record<string, string> = { dir_greed_up: "▲", dir_greed_down: "▼", dir_greed_left: "◀", dir_greed_right: "▶" };
  const POS_GLYPH: Record<string, string> = { row: "R", col: "C", surr: "S", diag: "X" };

  function clickCell(cell: (typeof cells)[number]) {
    if (canConvert && !cell.arcane && onConvert) onConvert([cell.pos[0], cell.pos[1]]);
  }

  function contextCell(e: MouseEvent, cell: (typeof cells)[number]) {
    if (cell.converted && onUnconvert) { e.preventDefault(); onUnconvert([cell.pos[0], cell.pos[1]]); return; }
    if (cell.added && onRemove) { e.preventDefault(); onRemove([cell.pos[0], cell.pos[1]]); }
  }

  function imgs(v: SlotView) {
    return cardImages(art, v.t, v.color, v.ident, tier);
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
  {#each candidates as p (key(p))}
    <button
      type="button"
      class="cand"
      style:left="{(p[1] - bounds.c0) * (CELL + GAP)}px"
      style:top="{(p[0] - bounds.r0) * (CELL + GAP)}px"
      style:width="{CELL}px"
      style:height="{CELL}px"
      aria-label="Add a Construction slot here"
      onclick={() => onPlace?.([p[0], p[1]])}
    >+</button>
  {/each}
  {#each cells as cell (cell.i)}
    {@const v = cell.view}
    {@const line = cardLine(v)}
    <div
      class="cell"
      class:arcane={cell.arcane}
      class:empty={!v}
      class:added={cell.added}
      class:convertible={canConvert && !cell.arcane}
      style:left="{cell.c * (CELL + GAP)}px"
      style:top="{cell.r * (CELL + GAP)}px"
      style:width="{CELL}px"
      style:height="{CELL}px"
      style:background={v && !imgs(v).base ? slotBg(v.t as CardType) : v ? "#1a2233" : "var(--bg-empty-slot)"}
      style:border-color={cell.arcane ? ARCANE_BORDER : cell.added ? "#94A3B8" : "transparent"}
      role="button"
      tabindex="-1"
      aria-label={v ? `${v.t} ${line.top} ${line.bottom}` : cell.arcane ? "arcane slot" : "empty slot"}
      onclick={() => clickCell(cell)}
      onkeydown={(e) => { if (e.key === "Enter") clickCell(cell); }}
      oncontextmenu={(e) => contextCell(e, cell)}
      onmouseenter={() => (hover = cell.i)}
      onmouseleave={() => (hover = null)}
    >
      {#if v && v.t !== "dead" && imgs(v).base}
        {@const im = imgs(v)}
        <img class="base" src={im.base} alt="" />
        {#if im.icon}<img class="ic" src={im.icon} alt="" />{/if}
        {#if ARROW[v.t]}<span class="ar">{ARROW[v.t]}</span>{/if}
        {#if POS_GLYPH[v.t]}<span class="pg">{POS_GLYPH[v.t]}</span>{/if}
        {#if v.ident?.kind === "deluxe"}<span class="dlx"></span>{/if}
        {#if v.ident?.grant}<span class="lv">+{v.ident.grant[2]}</span>{/if}
        {#if line.bottom && !v.ident?.grant}<span class="nv">{line.bottom}</span>{/if}
      {:else if v && v.t !== "dead"}
        {#if v.color && v.t !== "arcane"}
          <span class="cbar" style:background={COLOR_HEX[v.color as Color] ?? "transparent"}></span>
        {/if}
        <span class="glyph">{TYPE_GLYPH[v.t as CardType] ?? "?"}</span>
        <span class="top">{line.top}</span>
        <span class="bottom">{line.bottom}</span>
      {:else if v}
        <span class="glyph dead">_</span>
      {:else if cell.arcane}
        <span class="glyph dead">a</span>
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
        {#each hv.parts.adds as [src, a] (src)}
          <div class="row"><span>+ {sourceLabel(src)}</span><span>{fmt(a)}</span></div>
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
    color: #111827; overflow: hidden; font-variant-numeric: tabular-nums; outline: none;
  }
  .cell.empty { opacity: 0.6; }
  .cell.added { border-style: dashed; }
  .cell.convertible { cursor: pointer; }
  .cell.convertible:hover { box-shadow: 0 0 0 2px #A78BFA inset; }
  .cand {
    position: absolute; box-sizing: border-box; border: 1px dashed #64748B; border-radius: 6px; background: #4B5563;
    color: #F1F5F9; font-size: 18px; font-weight: 600; cursor: pointer; opacity: .55; padding: 0;
  }
  .cand:hover { opacity: 1; }
  .base { position: absolute; left: 1px; top: 1px; width: 52px; height: 52px; image-rendering: pixelated; }
  .ic { position: absolute; left: 13px; top: 10px; width: 28px; height: 28px; image-rendering: pixelated; }
  .ar { position: absolute; inset: 0; display: grid; place-items: center; font-size: 20px; color: #fde68a;
        text-shadow: 0 1px 3px #000; }
  .pg { position: absolute; left: 3px; top: 1px; font-size: 10px; font-weight: 800; color: #fff; text-shadow: 0 0 3px #000, 0 0 2px #000; }
  .lv { position: absolute; right: 3px; top: 1px; font-size: 11px; font-weight: 800; color: #fde68a; text-shadow: 0 0 3px #000, 0 0 2px #000; }
  .nv { position: absolute; left: 0; right: 0; bottom: 1px; text-align: center; font-size: 10px; font-weight: 700; color: #fff;
        text-shadow: 0 0 3px #000, 0 0 2px #000; white-space: nowrap; overflow: hidden; }
  .dlx { position: absolute; inset: 0; border-radius: 5px; box-shadow: inset 0 0 0 2px rgba(250, 204, 21, .6); pointer-events: none; }
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
