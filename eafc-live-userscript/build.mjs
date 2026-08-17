// build.mjs
//
// Bundles src/main.js (and everything it imports) into a single,
// human-readable eafc-live.user.js with `minify: false` - the user was
// explicit that the distributed userscript must stay auditable/readable,
// unlike autosbc.user.js's obfuscated body. The `==UserScript==` header
// (src/metadata.js) is prepended to the bundle output, since esbuild has
// no native concept of a userscript header - it's just a comment block
// that userscript managers parse specially.
//
// TWO SEPARATE BUNDLES, ONE FILE
// -------------------------------------------------------------------
// src/page-hook.js has to run in the EA page's TRUE JS realm regardless
// of what context the rest of the userscript ends up in (see that
// file's header - confirmed live that at least one real userscript
// manager doesn't expose GM_xmlhttpRequest/GM.xmlHttpRequest to a
// script actually running in page context). It's bundled to its own
// tiny IIFE here, independently of main.js's bundle, and its output
// text is inlined into main.js's bundle as a plain string constant via
// the virtual-module plugin below - main.js then injects that string as
// a real <script> tag's textContent at runtime (see market-observer.js),
// which always executes in the page's true realm no matter how the
// outer userscript itself is invoked. The two bundles never share
// module scope; a CustomEvent on `window` is the only channel between
// them, by design.
import { build } from "esbuild";
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { METADATA_BLOCK } from "./src/metadata.js";

const root = fileURLToPath(new URL(".", import.meta.url));
const outfile = `${root}dist/eafc-live.user.js`;

mkdirSync(`${root}dist`, { recursive: true });

const pageHookResult = await build({
  entryPoints: [`${root}src/page-hook.js`],
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: false,
  legalComments: "none",
});
const pageHookSource = pageHookResult.outputFiles[0].text;

const result = await build({
  entryPoints: [`${root}src/main.js`],
  bundle: true,
  write: false,
  format: "iife",
  platform: "browser",
  target: ["es2020"],
  minify: false,
  legalComments: "none",
  plugins: [
    {
      name: "inline-page-hook-source",
      setup(b) {
        b.onResolve({ filter: /^virtual:page-hook-source$/ }, (args) => ({
          path: args.path,
          namespace: "page-hook-source-ns",
        }));
        b.onLoad({ filter: /.*/, namespace: "page-hook-source-ns" }, () => ({
          contents: `export default ${JSON.stringify(pageHookSource)};`,
          loader: "js",
        }));
      },
    },
  ],
});

const bundled = result.outputFiles[0].text;
writeFileSync(outfile, METADATA_BLOCK + "\n" + bundled);

console.log(`[eafc-live-userscript] built ${outfile}`);
