// Keep the checked-in Companion userscript compatible with the active FC
// season before Vite copies frontend/public/ into dist/.
//
// The userscript source lives outside the frontend Docker build context,
// so the production image cannot run eafc-live-userscript/build.mjs itself.
// This deliberately narrow preparation step updates only the generated
// fragments changed by the FC27 compatibility fix and fails closed if the
// checked-in artifact no longer has the expected shape. That prevents a
// source change from silently shipping the old FC26-only observer.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const file = fileURLToPath(new URL("../public/companion/eafc-live.user.js", import.meta.url));
let source = readFileSync(file, "utf8");

function replaceRequired(oldValue, newValue, label) {
  if (!source.includes(oldValue)) {
    throw new Error(`[prepare-companion] expected ${label} was not found; rebuild the checked-in userscript from source before deploying`);
  }
  source = source.split(oldValue).join(newValue);
}

replaceRequired("// @version      1.9.1", "// @version      1.10.0", "userscript version");
replaceRequired(
  "EA SPORTS FC 26 Web App Transfer Market",
  "EA SPORTS FC Web App Transfer Market",
  "season-specific metadata description",
);
replaceRequired("FC26_RE", "FC_GAME_RE", "FC26 observer constant");
replaceRequired(
  "var FC_GAME_RE = /\\\\/ut\\\\/game\\\\/fc26(\\\\/|$)/;",
  "var FC_GAME_RE = /\\\\/ut\\\\/game\\\\/fc\\\\d+(\\\\/|$)/i;",
  "FC26 API-path matcher",
);
replaceRequired(
  "observing window.fetch/XHR for /ut/game/fc26 responses",
  "observing active /ut/game/fc*/ responses",
  "FC26 observer log text",
);

writeFileSync(file, source);
console.log("[prepare-companion] prepared season-neutral Companion userscript v1.10.0");
