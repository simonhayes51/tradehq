// scripts/build.mjs
//
// Bundles src/app.js (which needs a real `import` of
// @capgo/capacitor-inappbrowser - its plugin-name resolution is
// dynamic per platform, so a hand-rolled `window.Capacitor.Plugins.X`
// lookup isn't safe to guess at, see eafc-live-mobile/README.md) into
// www/app.js, which is the only JS Capacitor's WebView actually loads
// (webDir is "www" - node_modules is never bundled into the native app
// on its own).
//
// shared/ea-badge.js - the script actually injected into the EA page -
// is pulled in as a plain string via a tiny virtual-module esbuild
// plugin, not bundled as executable code itself: it never runs in this
// app's own WebView, only ever passed as a `code` string to
// executeScript() against the EA in-app browser.
import { build } from "esbuild";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const badgeSource = readFileSync(`${root}/shared/ea-badge.js`, "utf8");

await build({
  entryPoints: [`${root}/src/app.js`],
  bundle: true,
  outfile: `${root}/www/app.js`,
  format: "iife",
  platform: "browser",
  target: ["ios14", "chrome90"],
  plugins: [
    {
      name: "inline-ea-badge-script",
      setup(b) {
        b.onResolve({ filter: /^virtual:ea-badge-script$/ }, (args) => ({
          path: args.path,
          namespace: "ea-badge-script-ns",
        }));
        b.onLoad({ filter: /.*/, namespace: "ea-badge-script-ns" }, () => ({
          contents: `export default ${JSON.stringify(badgeSource)};`,
          loader: "js",
        }));
      },
    },
  ],
});

console.log("[eafc-live-mobile] built www/app.js");
