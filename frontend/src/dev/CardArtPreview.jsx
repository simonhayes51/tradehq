// Dev-only visual harness for PlayerCardArt.jsx - renders it in every
// container context the real app uses it in (Home hero, dashboard modal,
// queue-list compact, strategy-card compact), against both dark and
// light/cream card-frame backgrounds, with base vs special cutoutType,
// and the no-bgImage fallback path - so rendering bugs can be caught by
// looking at this page instead of round-tripping phone screenshots.
// Not routed from App.v2 - only reachable by importing directly (see
// devMain.jsx) and not part of the production bundle.
import PlayerCardArt from "../components/PlayerCardArt";
import "../index.css";
import "../v2/styles/dashboard-v2.css";

const darkBg =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="300" height="420">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#1b3a2e"/>
          <stop offset="1" stop-color="#0a1f16"/>
        </linearGradient>
      </defs>
      <path d="M20 40 Q150 0 280 40 L280 380 Q150 420 20 380 Z" fill="url(#g)" stroke="#d4c98a" stroke-width="6"/>
    </svg>
  `);

const lightBg =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="300" height="420">
      <defs>
        <linearGradient id="g2" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#fdfaf0"/>
          <stop offset="1" stop-color="#f1e8c8"/>
        </linearGradient>
      </defs>
      <path d="M20 40 Q150 0 280 40 L280 380 Q150 420 20 380 Z" fill="url(#g2)" stroke="#ccbb77" stroke-width="6"/>
    </svg>
  `);

const goldBaseBg =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="300" height="420">
      <defs>
        <linearGradient id="g3" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#e8c766"/>
          <stop offset="1" stop-color="#b8912f"/>
        </linearGradient>
      </defs>
      <path d="M20 40 Q150 0 280 40 L280 380 Q150 420 20 380 Z" fill="url(#g3)" stroke="#7a5a10" stroke-width="6"/>
    </svg>
  `);

const cutoutSpecial =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="300" height="420">
      <circle cx="150" cy="150" r="70" fill="#c9906a"/>
      <path d="M60 420 Q150 260 240 420 Z" fill="#3a4a6b"/>
    </svg>
  `);

const cutoutBaseHeadshot =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="220" height="260">
      <rect width="220" height="260" fill="#111"/>
      <circle cx="110" cy="110" r="60" fill="#c9906a"/>
      <path d="M40 260 Q110 170 180 260 Z" fill="#3a4a6b"/>
    </svg>
  `);

const badge = (color) =>
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="${color}"/></svg>`);

const stats = { pace: 93, shooting: 90, passing: 91, dribbling: 92, defending: 66, physicality: 76 };

function Labeled({ label, children }) {
  return (
    <div style={{ display: "inline-flex", flexDirection: "column", alignItems: "center", gap: 8, margin: 16 }}>
      <span style={{ color: "#9aa", font: "12px monospace" }}>{label}</span>
      <div style={{ background: "#000", padding: 12 }}>{children}</div>
    </div>
  );
}

export default function CardArtPreview() {
  const common = {
    rating: 91, position: "RW", name: "Ethan Nwaneri", stats,
    nationImage: badge("#0a2f6b"), leagueImage: badge("#8b0000"), clubImage: badge("#1a6b1a"),
    altText: "Ethan Nwaneri",
  };

  return (
    <div style={{ background: "#101010", minHeight: "100vh", padding: 24, display: "flex", flexWrap: "wrap" }}>
      <Labeled label="featured / dark bg / special cutout (the working case)">
        <div className="lead-card">
          <PlayerCardArt {...common} bgImage={darkBg} cutoutImage={cutoutSpecial} cutoutType="special" featured widthClass="w-52" />
        </div>
      </Labeled>

      <Labeled label="featured / LIGHT (Icon-style) bg / special cutout - the bug case">
        <div className="lead-card">
          <PlayerCardArt {...common} name="Marinette Pichon" rating={90} position="ST" bgImage={lightBg} cutoutImage={cutoutSpecial} cutoutType="special" featured widthClass="w-52" />
        </div>
      </Labeled>

      <Labeled label="featured / gold base bg / base cutout (headshot, common gold)">
        <div className="lead-card">
          <PlayerCardArt {...common} name="Joaquín Fernández" rating={73} position="CB" bgImage={goldBaseBg} cutoutImage={cutoutBaseHeadshot} cutoutType="base" featured widthClass="w-52" />
        </div>
      </Labeled>

      <Labeled label="dash-modal-card featured (the analysis modal container)">
        <div className="dash-modal-card" style={{ width: 260, height: 420, display: "grid", placeItems: "center" }}>
          <PlayerCardArt {...common} bgImage={darkBg} cutoutImage={cutoutSpecial} cutoutType="special" featured widthClass="w-52" />
        </div>
      </Labeled>

      <Labeled label="compact / dark bg / special (queue-art, ~72px column)">
        <div className="queue-art">
          <PlayerCardArt {...common} bgImage={darkBg} cutoutImage={cutoutSpecial} cutoutType="special" compact widthClass="w-16" showStats={false} />
        </div>
      </Labeled>

      <Labeled label="compact / LIGHT bg / special (the doubled-digit bug case)">
        <div className="queue-art">
          <PlayerCardArt {...common} rating={90} bgImage={lightBg} cutoutImage={cutoutSpecial} cutoutType="special" compact widthClass="w-16" showStats={false} />
        </div>
      </Labeled>

      <Labeled label="compact / gold base bg / base cutout (headshot)">
        <div className="queue-art">
          <PlayerCardArt {...common} rating={87} bgImage={goldBaseBg} cutoutImage={cutoutBaseHeadshot} cutoutType="base" compact widthClass="w-16" showStats={false} />
        </div>
      </Labeled>

      <Labeled label="compact / narrow tall photo vs wide frame (Tijjani black-bar repro)">
        <div className="queue-art">
          <PlayerCardArt
            {...common} rating={87}
            bgImage={goldBaseBg}
            cutoutImage={
              "data:image/svg+xml;utf8," +
              encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="80" height="400"><rect width="80" height="400" fill="#000"/><circle cx="40" cy="90" r="30" fill="#c9906a"/></svg>`)
            }
            cutoutType="special"
            compact widthClass="w-16" showStats={false}
          />
        </div>
      </Labeled>

      <Labeled label="fallback (no bgImage) / compact - common card, no card_design_image_url">
        <div className="queue-art">
          <PlayerCardArt rating={68} name="Gustavo Miguel" fallbackImage={cutoutBaseHeadshot} altText="Gustavo Miguel" compact widthClass="w-16" />
        </div>
      </Labeled>

      <Labeled label="fallback (no bgImage) / featured">
        <div className="lead-card">
          <PlayerCardArt rating={68} name="Gustavo Miguel" fallbackImage={cutoutBaseHeadshot} altText="Gustavo Miguel" featured widthClass="w-52" />
        </div>
      </Labeled>
    </div>
  );
}
