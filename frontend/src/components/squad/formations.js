// src/components/squad/formations.js

// Coordinates are percentage positions on a 100x100 pitch (0,0 top-left)
export const FORMATIONS = {
  "4-3-3": [
    // Attack
    { key: "LW",  pos: "LW",  x: 22, y: 24 },
    { key: "ST",  pos: "ST",  x: 50, y: 18 },
    { key: "RW",  pos: "RW",  x: 78, y: 24 },

    // Midfield
    { key: "LCM", pos: "CM",  x: 35, y: 42 },
    { key: "CM",  pos: "CM",  x: 50, y: 46 },
    { key: "RCM", pos: "CM",  x: 65, y: 42 },

    // Defence
    { key: "LB",  pos: "LB",  x: 22, y: 68 },
    { key: "LCB", pos: "CB",  x: 37, y: 78 },
    { key: "RCB", pos: "CB",  x: 63, y: 78 },
    { key: "RB",  pos: "RB",  x: 78, y: 68 },

    // GK
    { key: "GK",  pos: "GK",  x: 50, y: 92 },
  ],

  "4-2-3-1": [
    { key: "ST",  pos: "ST",  x: 50, y: 18 },

    { key: "LAM", pos: "CAM", x: 32, y: 30 },
    { key: "CAM", pos: "CAM", x: 50, y: 32 },
    { key: "RAM", pos: "CAM", x: 68, y: 30 },

    { key: "LCDM", pos: "CDM", x: 40, y: 48 },
    { key: "RCDM", pos: "CDM", x: 60, y: 48 },

    { key: "LB",  pos: "LB",  x: 22, y: 68 },
    { key: "LCB", pos: "CB",  x: 37, y: 78 },
    { key: "RCB", pos: "CB",  x: 63, y: 78 },
    { key: "RB",  pos: "RB",  x: 78, y: 68 },

    { key: "GK",  pos: "GK",  x: 50, y: 92 },
  ],

  "4-4-2": [
    { key: "LST", pos: "ST", x: 42, y: 20 },
    { key: "RST", pos: "ST", x: 58, y: 20 },

    { key: "LM",  pos: "LM", x: 27, y: 38 },
    { key: "LCM", pos: "CM", x: 42, y: 44 },
    { key: "RCM", pos: "CM", x: 58, y: 44 },
    { key: "RM",  pos: "RM", x: 73, y: 38 },

    { key: "LB",  pos: "LB", x: 22, y: 68 },
    { key: "LCB", pos: "CB", x: 37, y: 78 },
    { key: "RCB", pos: "CB", x: 63, y: 78 },
    { key: "RB",  pos: "RB", x: 78, y: 68 },

    { key: "GK",  pos: "GK", x: 50, y: 92 },
  ],

  "3-5-2": [
    { key: "LST", pos: "ST", x: 45, y: 18 },
    { key: "RST", pos: "ST", x: 55, y: 18 },

    { key: "LM",  pos: "LM",  x: 26, y: 34 },
    { key: "LCM", pos: "CM",  x: 40, y: 42 },
    { key: "CAM", pos: "CAM", x: 50, y: 34 },
    { key: "RCM", pos: "CM",  x: 60, y: 42 },
    { key: "RM",  pos: "RM",  x: 74, y: 34 },

    { key: "LCB", pos: "CB", x: 37, y: 74 },
    { key: "CB",  pos: "CB", x: 50, y: 78 },
    { key: "RCB", pos: "CB", x: 63, y: 74 },

    { key: "GK",  pos: "GK", x: 50, y: 92 },
  ],
};