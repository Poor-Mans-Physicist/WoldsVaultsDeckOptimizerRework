// Score parity: every fixture from parity_fixtures.py scored through the WASM kernel (Node build).
//
//   wasm-pack build --target nodejs --release --out-dir <dir> -- --no-default-features --features wasm
//   node wasm-port/scripts/parity_check.mjs <dir>/ndm_core.js <fixtures.json>
//
// Also checks that explainTagged's slot terms sum to the NDM score on NDM fixtures.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const [, , modPath, fixPath] = process.argv;
const wasm = require(modPath);
const { fixtures } = JSON.parse(readFileSync(fixPath, "utf8"));

let worst = 0;
let bad = 0;
for (const f of fixtures) {
  const got = wasm.scoreTagged(f.input);
  const err = Math.abs(got - f.expected) / Math.max(1, Math.abs(f.expected));
  worst = Math.max(worst, err);
  if (err > 1e-9) {
    bad++;
    console.log(`MISMATCH ${f.stage} ${f.deck} [${f.cores}] ${f.objective}: wasm ${got} python ${f.expected}`);
  }
  if (f.objective === "ndm") {
    const ex = wasm.explainTagged(f.input);
    const sum = ex.slots.reduce((s, p) => s + p.term, 0);
    if (Math.abs(sum - got) > 1e-9 * Math.max(1, Math.abs(got)) || Math.abs(ex.score - got) > 1e-12) {
      bad++;
      console.log(`EXPLAIN ${f.stage} ${f.deck}: slot terms ${sum}, explain ${ex.score}, score ${got}`);
    }
  }
}
console.log(`${fixtures.length} fixtures, ${bad} mismatches, worst relative error ${worst.toExponential(2)}`);
process.exit(bad ? 1 : 0);
