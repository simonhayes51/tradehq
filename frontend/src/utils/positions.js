// Canonical position codes
const POSITION_SET = new Set([
  "GK",
  "RB", "RWB", "CB", "LB", "LWB",
  "CDM", "CM", "CAM",
  "RM", "LM",
  "RW", "LW",
  "RF", "LF", "CF", "ST"
]);

// Aliases → canonical codes
const ALIASES = new Map([
  ["GOALKEEPER","GK"], ["KEEPER","GK"], ["GK","GK"],

  ["RIGHT BACK","RB"], ["RIGHTBACK","RB"], ["RB","RB"],
  ["LEFT BACK","LB"], ["LEFTBACK","LB"], ["LB","LB"],
  ["RIGHT WING BACK","RWB"], ["RIGHTWINGBACK","RWB"], ["RWB","RWB"],
  ["LEFT WING BACK","LWB"], ["LEFTWINGBACK","LWB"], ["LWB","LWB"],
  ["CENTER BACK","CB"], ["CENTRE BACK","CB"], ["CENTERBACK","CB"], ["CENTREBACK","CB"], ["CB","CB"],

  ["DEFENSIVE MID","CDM"], ["DEFENSIVE MIDFIELDER","CDM"], ["CDM","CDM"],
  ["CENTRE MID","CM"], ["CENTER MID","CM"], ["CENTREMID","CM"], ["CENTERMID","CM"], ["CM","CM"],
  ["ATTACKING MID","CAM"], ["ATTACKING MIDFIELDER","CAM"], ["CAM","CAM"],

  ["RIGHT MID","RM"], ["RIGHTMID","RM"], ["RM","RM"],
  ["LEFT MID","LM"], ["LEFTMID","LM"], ["LM","LM"],

  ["RIGHT WING","RW"], ["RIGHTWING","RW"], ["RW","RW"],
  ["LEFT WING","LW"], ["LEFTWING","LW"], ["LW","LW"],

  ["RIGHT FORWARD","RF"], ["RIGHTFORWARD","RF"], ["RF","RF"],
  ["LEFT FORWARD","LF"], ["LEFTFORWARD","LF"], ["LF","LF"],
  ["CENTRE FORWARD","CF"], ["CENTER FORWARD","CF"], ["CENTREFORWARD","CF"], ["CENTERFORWARD","CF"], ["CF","CF"],

  ["STRIKER","ST"], ["FORWARD","ST"], ["ST","ST"]
]);

export function normalizePosition(p) {
  if (p == null) return null;
  const cleaned = String(p).trim().toUpperCase();
  if (POSITION_SET.has(cleaned)) return cleaned;

  if (ALIASES.has(cleaned)) return ALIASES.get(cleaned);

  const noSpaces = cleaned.replace(/\s+/g, "");
  if (ALIASES.has(noSpaces)) return ALIASES.get(noSpaces);

  const lettersOnly = cleaned.replace(/[^A-Z]/g, "");
  if (POSITION_SET.has(lettersOnly)) return lettersOnly;
  if (ALIASES.has(lettersOnly)) return ALIASES.get(lettersOnly);

  return null;
}

export function normalizePositions(list) {
  const seen = new Set();
  const out = [];
  (Array.isArray(list) ? list : [list]).forEach((p) => {
    const norm = normalizePosition(p);
    if (norm && !seen.has(norm)) {
      seen.add(norm);
      out.push(norm);
    }
  });
  return out;
}

export const POSITIONS = Array.from(POSITION_SET);
export const isPosition = (p) => POSITION_SET.has(String(p || "").toUpperCase());

/** slotPosition (string), playerPositions (array|string) */
export function isValidForSlot(slotPosition, playerPositions) {
  const slot = normalizePosition(slotPosition);
  if (!slot) return false;

  const list = normalizePositions(playerPositions);
  // compat map
  const COMPATIBILITY = {
    GK: ["GK"],

    RB: ["RB","RWB","CB"],
    RWB:["RWB","RB","RM"],
    CB: ["CB","RB","LB"],
    LB: ["LB","LWB","CB"],
    LWB:["LWB","LB","LM"],

    CDM:["CDM","CM","CB"],
    CM: ["CM","CDM","CAM"],
    CAM:["CAM","CM","CF"],

    RM: ["RM","RW","RWB","CM"],
    LM: ["LM","LW","LWB","CM"],

    RW: ["RW","RM","RF","ST"],
    LW: ["LW","LM","LF","ST"],
    RF: ["RF","CF","RW","ST"],
    LF: ["LF","CF","LW","ST"],
    CF: ["CF","CAM","ST","RF","LF"],
    ST: ["ST","CF","RF","LF","RW","LW"]
  };

  for (const p of list) {
    if (p === slot) return true;
    const compat = COMPATIBILITY[p] || [];
    if (compat.includes(slot)) return true;
  }
  return false;
}