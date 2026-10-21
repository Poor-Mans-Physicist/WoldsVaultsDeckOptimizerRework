// Card textures for the deck grid, as the build ledger draws them: the game's card base for the card's kind, colour
// and tier, the stat icon (stat cards) or the card's own art (Knack, Arcane, Temporal, Resource) on top. Loaded once
// from public/rework/card_art.json (wv-combat-model tools/site_bundle.py, from the ledger's extracted assets).

import type { StackIdent } from "./types";

export type CardArt = Record<string, string>;

let _art: Promise<CardArt> | null = null;

export function loadCardArt(baseUrl: string): Promise<CardArt> {
  if (!_art) {
    _art = fetch(`${baseUrl}rework/card_art.json`, { cache: "no-cache" })
      .then((r) => {
        if (!r.ok) throw new Error(`card_art.json: ${r.status}`);
        return r.json() as Promise<CardArt>;
      })
      .catch((e) => {
        console.error("[rework] FALLBACK: card art unavailable; plain tiles used", e);
        return {} as CardArt;
      });
  }
  return _art;
}

export interface CardImages {
  base: string | null;
  icon: string | null;
}

/** Base texture and overlay icon of a placed card (null when the art file lacks them). */
export function cardImages(art: CardArt, t: string, color: string, id: StackIdent | null, tier: number): CardImages {
  const col = color || "yellow";
  if (t.startsWith("dir_greed")) return { base: art["card:greed"] ?? null, icon: null };
  if (!id || t === "dead") return { base: null, icon: null };
  let base: string | undefined;
  if (id.kind === "evo") base = art[`card:evo:${col}:${tier}`];
  else if (id.kind === "arcane") base = art[`card:arcane:${col}`];
  else if (id.kind === "battery" && id.id === "Temporal") base = art[`card:temporal:${col}:${tier}`] ?? art[`card:temporal:${col}:1`];
  else if (id.kind === "battery" && id.id === "Resource") base = art[`card:resource:${col}`];
  else base = art[`card:stat:${col}:${tier}`];
  const icon = id.attr ? art[`cardicon:${id.attr.split(":").pop()}`] : art[`cardart:${id.id}`];
  return { base: base ?? null, icon: icon ?? null };
}
