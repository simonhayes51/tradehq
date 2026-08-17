// build.mjs
//
// Bundles src/main.js (and everything it imports) into a single,
// human-readable eafc-live.user.js with `minify: false` - the user was
// explicit that the distributed userscript must stay auditable/readable,
// unlike autosbc.user.js's obfuscated body. The `==UserScript==` header
// (src/metadata.js) is prepended to the bundle output, since esbuild has
// no native concept of a userscript header - it's just a comment block
// that userscript managers parse specially.
import { build } from "esbuild";
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { METADATA_BLOCK } from "./src/metadata.js";

const root = fileURLToPath(new URL(".", import.meta.url));
const outfile = `${root}dist/eafc-live.user.js`;

mkdirSync(`${root}dist`, { recursive: true });

const result = await build({
  entryPoints: [`${root}src/main.js`],
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: false,
  legalComments: "none",
});

const bundled = result.outputFiles[0].text;
writeFileSync(outfile, METADATA_BLOCK + "\n" + bundled);

console.log(`[eafc-live-userscript] built ${outfile}`);
