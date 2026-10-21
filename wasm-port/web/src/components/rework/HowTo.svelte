<script lang="ts">
  // Short how-to guide and the data provenance, under the three columns.
  import type { BundleMeta } from "../../lib/rework/types";

  interface Props {
    meta: BundleMeta | null;
  }
  let { meta }: Props = $props();
</script>

<footer class="howto">
  <h2>How to use</h2>
  <ol>
    <li><b>Pick a mode.</b> Global uses a game stage's rules. Targeted and Exact use the core values you type in, plus
      tag rules (Targeted) or the exact cards you own (Exact).</li>
    <li><b>Pick a stage and a playstyle.</b> Balanced suits every build; Melee, Caster, Physical abilities and Thorns &amp;
      Block weight the decks those builds want; Loot maximises omega and mythic rolls in mapped chests.</li>
    <li><b>Pick your deck</b>, or tick <i>Search all my decks</i>. For a Mystery deck, fix your modifier pair or let it
      search the pair.</li>
    <li><b>Untick cores you don't own.</b> The search only builds core sets from the ticked ones.</li>
    <li><b>Optimize.</b> The deck appears in the centre; hover a card for how its value is built. The right column lists
      the cards and the deck's total stats.</li>
  </ol>
  <p class="note">
    Scores come from the Wold's Vaults build ledger: how many hyperboss cycles each ledger build gains or loses as the
    deck's stats change, averaged over the playstyle's builds. They assume the ledger's gear, so treat them as a guide.
    Loot counts pool hits in the four regular mapped chests and leaves out medallions, crystal modifiers and potions.
    {#if meta}<br />Data: ledger {meta.ledger_commit || "?"}, results {meta.results_generated}, bundle {meta.bundle_generated}.{/if}
  </p>
</footer>

<style>
  .howto { max-width: 980px; margin: 8px auto 32px; padding: 0 16px; color: var(--text-secondary); font-size: 13px; line-height: 1.5; }
  h2 { font-size: 14px; color: var(--text-primary); margin: 0 0 6px; }
  ol { margin: 0; padding-left: 20px; }
  li { margin: 2px 0; }
  .note { color: var(--text-muted); font-size: 12px; margin-top: 10px; }
</style>
