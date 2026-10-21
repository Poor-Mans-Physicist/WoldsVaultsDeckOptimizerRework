<script lang="ts">
  // Short how-to guide and the data provenance, in the centre column under the deck (and the core-set box when a run
  // has filled it).
  import type { BundleMeta } from "../../lib/rework/types";

  interface Props {
    meta: BundleMeta | null;
  }
  let { meta }: Props = $props();
</script>

<section class="howto">
  <h3>How to use</h3>
  <ol>
    <li><b>Pick a mode.</b> Global uses a game stage's rules. Targeted and Exact use the core values you type in, plus
      tag rules (Targeted) or the exact cards you own (Exact).</li>
    <li><b>Pick a stage and a playstyle.</b> Balanced suits every build; Melee, Caster, Physical abilities and Thorns &amp;
      Block weight the decks those builds want; Loot maximises omega and mythic rolls in mapped chests.</li>
    <li><b>Pick your deck</b>, or tick <i>Search all my decks</i>. For a Mystery deck, fix your modifier pair or let it
      search the pair. Construction and Arcane cores reshape the deck on the grid before a run.</li>
    <li><b>Untick cores you don't own.</b> The search only builds core sets from the ticked ones.</li>
    <li><b>Optimize.</b> Hover a card for how its value is built; the right column lists the cards and the deck's totals.</li>
  </ol>
  <p class="note">
    Scores come from the Wold's Vaults build ledger: how many hyperboss cycles each ledger build gains or loses as the
    deck's stats change, averaged over the playstyle's builds. They assume the ledger's gear, so treat them as a guide.
    Loot counts pool hits in the four regular mapped chests and leaves out medallions, crystal modifiers and potions.
    {#if meta}<br />Data: ledger {meta.ledger_commit || "?"}, results {meta.results_generated}, bundle {meta.bundle_generated}.{/if}
  </p>
</section>

<style>
  .howto {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 12px;
    color: var(--text-secondary); font-size: 13px; line-height: 1.5;
  }
  h3 { margin: 0 0 6px; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-secondary); font-weight: 600; }
  ol { margin: 0; padding-left: 20px; }
  li { margin: 2px 0; }
  .note { color: var(--text-muted); font-size: 12px; margin: 10px 0 0; }
</style>
