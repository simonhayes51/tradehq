// src/components/squad/formations_vertical.js
// Vertical formations with canonical `pos` per slot (required for chemistry)

function mapKeyToPos(key) {
  const k = String(key).toUpperCase();

  // order matters (avoid 'LW' matching inside 'LWB', etc.)
  if (k.includes("LWB")) return "LWB";
  if (k.includes("RWB")) return "RWB";

  if (k.includes("LB")) return "LB";
  if (k.includes("RB")) return "RB";

  if (k.includes("LCB") || k === "CB" || k.includes("RCB")) return "CB";

  if (k.includes("LCDM") || k === "CDM" || k.includes("RCDM")) return "CDM";

  if (k.includes("LCM") || k === "CM" || k.includes("RCM")) return "CM";

  if (k.includes("LCAM") || k === "CAM" || k.includes("RCAM")) return "CAM";
  if (k.includes("LAM") || k.includes("RAM")) return "CAM"; // EA wide AMs count as CAM row

  if (k === "LM") return "LM";
  if (k === "RM") return "RM";

  if (k === "LW") return "LW";
  if (k === "RW") return "RW";

  if (k === "LF") return "LF";
  if (k === "RF") return "RF";
  if (k === "CF") return "CF";

  if (k.includes("LST") || k === "ST" || k.includes("RST")) return "ST";

  if (k === "GK") return "GK";

  // fallback: if it's a known pure code already, keep it
  const KNOWN = new Set(["GK","RB","RWB","CB","LB","LWB","CDM","CM","CAM","RM","LM","RW","LW","RF","LF","CF","ST"]);
  if (KNOWN.has(k)) return k;

  // default safest guess
  return k;
}

function line(keys, y, xStart = 15, xEnd = 85) {
  const c = keys.length;
  if (c === 1) return [{ key: keys[0], x: 50, y, pos: mapKeyToPos(keys[0]) }];
  return keys.map((k, i) => ({
    key: k,
    x: Math.round(xStart + (i * (xEnd - xStart)) / (c - 1)),
    y,
    pos: mapKeyToPos(k),
  }));
}

// Better spaced lines
const GK_LINE     = (y = 92) => line(["GK"], y);
const BACK_FOUR   = (y = 75) => line(["LB", "LCB", "RCB", "RB"], y, 18, 82);
const BACK_THREE  = (y = 75) => line(["LCB", "CB", "RCB"], y, 25, 75);
const BACK_FIVE   = (y = 76) => line(["LWB","LCB","CB","RCB","RWB"], y, 10, 90);

const MF_TWO      = (y = 55) => line(["LCM","RCM"], y, 30, 70);
const MF_THREE    = (y = 52) => line(["LCM","CM","RCM"], y, 25, 75);
const MF_FOUR     = (y = 52) => line(["LM","LCM","RCM","RM"], y, 18, 82);
const MF_FIVE     = (y = 48) => line(["LM","LCM","CM","RCM","RM"], y, 12, 88);

const CAM_LINE    = (y = 35) => line(["CAM"], y);
const CAM_THREE   = (y = 37) => line(["LCAM","CAM","RCAM"], y, 28, 72);
const WIDE_AM     = (y = 33) => line(["LW","CAM","RW"], y, 20, 80);

const FRONT_ONE   = (y = 15) => line(["ST"], y);
const FRONT_TWO   = (y = 18) => line(["LST","RST"], y, 35, 65);
const FRONT_THREE = (y = 20) => line(["LW","ST","RW"], y, 20, 80);

export const VERTICAL_COORDS = {
  // --- 4-3-3 family --------------------------------------------------------
  "4-3-3": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...MF_THREE(52),
    ...FRONT_THREE(20),
  ],
  "4-3-3 (2)": [ // holding
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCM","RCM"], 57, 28, 72),
    ...line(["CDM"], 65, 50, 50),
    ...FRONT_THREE(20),
  ],
  "4-3-3 (3)": [ // false 9
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...MF_THREE(52),
    ...line(["LW","CF","RW"], 22, 20, 80),
  ],
  "4-3-3 (4)": [ // attack
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCM","RCM"], 57, 28, 72),
    ...CAM_LINE(40),
    ...FRONT_THREE(20),
  ],
  "4-3-3 (5)": [ // defend
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCDM","RCDM"], 62, 30, 70),
    ...line(["CM"], 48, 50, 50),
    ...FRONT_THREE(20),
  ],

  // --- 4-2-3-1 -------------------------------------------------------------
  "4-2-3-1": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCDM","RCDM"], 62, 30, 70),
    ...line(["LAM","CAM","RAM"], 38, 25, 75),
    ...FRONT_ONE(18),
  ],
  "4-2-3-1 (2)": [ // wide
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCDM","RCDM"], 62, 30, 70),
    ...line(["LM","CAM","RM"], 39, 18, 82),
    ...FRONT_ONE(18),
  ],

  // --- 4-4-2 ---------------------------------------------------------------
  "4-4-2": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LM","LCM","RCM","RM"], 52, 18, 82),
    ...FRONT_TWO(20),
  ],
  "4-4-2 (2)": [ // holding
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCDM","RCDM"], 60, 30, 70),
    ...line(["LM","RM"], 48, 20, 80),
    ...FRONT_TWO(20),
  ],

  // --- 4-1-2-1-2 -----------------------------------------------------------
  "4-1-2-1-2": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["CDM"], 62, 50, 50),
    ...line(["LCM","RCM"], 48, 28, 72),
    ...CAM_LINE(33),
    ...FRONT_TWO(18),
  ],
  "4-1-2-1-2 (2)": [ // wide
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["CDM"], 62, 50, 50),
    ...line(["LM","RM"], 48, 22, 78),
    ...CAM_LINE(33),
    ...FRONT_TWO(18),
  ],

  // --- 4-3-1-2 / 4-3-2-1 ---------------------------------------------------
  "4-3-1-2": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...MF_THREE(52),
    ...CAM_LINE(35),
    ...FRONT_TWO(18),
  ],
  "4-3-2-1": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...MF_THREE(52),
    ...line(["LF","RF"], 30, 32, 68),
    ...FRONT_ONE(16),
  ],

  // --- 4-5-1 ---------------------------------------------------------------
  "4-5-1": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...MF_FIVE(48),
    ...FRONT_ONE(18),
  ],
  "4-5-1 (2)": [ // 2 CDM + CAM
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCDM","RCDM"], 60, 30, 70),
    ...line(["LM","CAM","RM"], 42, 18, 82),
    ...FRONT_ONE(18),
  ],

  // --- 4-1-4-1 --------------------------------------------------------------
  "4-1-4-1": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["CDM"], 60, 50, 50),
    ...line(["LM","LCM","RCM","RM"], 45, 18, 82),
    ...FRONT_ONE(18),
  ],

  // --- 4-2-2-2 --------------------------------------------------------------
  "4-2-2-2": [
    ...GK_LINE(92),
    ...BACK_FOUR(75),
    ...line(["LCDM","RCDM"], 60, 30, 70),
    ...line(["LAM","RAM"], 40, 28, 72),
    ...FRONT_TWO(18),
  ],

  // --- 3 at the back formations --------------------------------------------
  "3-5-2": [
    ...GK_LINE(92),
    ...BACK_THREE(75),
    ...MF_FIVE(48),
    ...FRONT_TWO(20),
  ],
  "3-4-3": [
    ...GK_LINE(92),
    ...BACK_THREE(75),
    ...MF_FOUR(52),
    ...FRONT_THREE(22),
  ],
  "3-4-2-1": [
    ...GK_LINE(92),
    ...BACK_THREE(75),
    ...MF_FOUR(52),
    ...line(["LF","RF"], 30, 32, 68),
    ...FRONT_ONE(16),
  ],
  "3-4-1-2": [
    ...GK_LINE(92),
    ...BACK_THREE(75),
    ...MF_FOUR(52),
    ...CAM_LINE(35),
    ...FRONT_TWO(18),
  ],

  // --- 5 at the back formations --------------------------------------------
  "5-2-1-2": [
    ...GK_LINE(92),
    ...BACK_FIVE(76),
    ...MF_TWO(58),
    ...CAM_LINE(37),
    ...FRONT_TWO(20),
  ],
  "5-2-2-1": [
    ...GK_LINE(92),
    ...BACK_FIVE(76),
    ...MF_TWO(58),
    ...line(["LW","RW"], 37, 22, 78),
    ...FRONT_ONE(18),
  ],
  "5-3-2": [
    ...GK_LINE(92),
    ...BACK_FIVE(76),
    ...MF_THREE(52),
    ...FRONT_TWO(20),
  ],
  "5-4-1": [
    ...GK_LINE(92),
    ...BACK_FIVE(76),
    ...line(["LM","LCM","RCM","RM"], 52, 18, 82),
    ...FRONT_ONE(20),
  ],
};