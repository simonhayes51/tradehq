// src/components/PlayerCardArt.jsx
// Shared FUT-card fallback rendering (composited from raw bg/cutout layers)
// used across the app wherever a generated card PNG isn't ready to show yet.
// The actual PNG-export render target is PlayerCardExportArt.jsx /
// PlayerCardExport.jsx (internal render route) - this component only ever
// renders the on-screen fallback composition.
import React, { useState } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "";
const buildProxy = (url) => `${API_BASE}/img?url=${encodeURIComponent(url)}`;

function imageErrorFallback(originalUrl) {
  return (event) => {
    if (!event.currentTarget.dataset.triedProxy && originalUrl) {
      event.currentTarget.dataset.triedProxy = "1";
      event.currentTarget.src = buildProxy(originalUrl);
    }
  };
}

export default function PlayerCardArt({
  bgImage,
  cutoutImage,
  cutoutType,
  fallbackImage,
  rating,
  position,
  name,
  stats,
  nationImage,
  leagueImage,
  clubImage,
  versionLabel,
  altText,
  widthClass = "w-48",
  compact = false,
  featured = false,
  showStats = true,
  altPositions,
}) {
  const [cardAspect, setCardAspect] = useState(0.75);

  // Stable hook classes (independent of the widthClass Tailwind utility)
  // so page-specific CSS can size this component responsively per
  // breakpoint - mirrors the .ft-card-img.featured/.compact convention
  // the plain-<img> generatedCardUrl path already used, which callers
  // like the dashboard modal and Home hero card rely on to size this
  // component down at narrow viewports (Tailwind's w-* utilities are
  // fixed and don't respond to a container's own media queries).
  const sizeHookClass = featured ? "player-card-art featured" : compact ? "player-card-art compact" : "player-card-art";

  // A white fill with a soft black glow (blurred, not a hard offset
  // outline) reads on any card background, dark or light -
  // cutoutType-based black/white text (the previous approach) assumed
  // "base" always means a light/gold backdrop and "special" always means
  // a dark/vivid one, which holds for FUTBIN's own card set but not
  // FUT.GG's: Icon and other light-templated cards get classified
  // "special" (see dashboard.py's price_tier heuristic) and white text on
  // their white/cream background was reading as a near-invisible
  // embossed ghost. A first attempt at this used a hard 4-direction 1px
  // offset outline instead of blur - technically legible, but it reads as
  // a chunky sticker outline at normal size and, worse, at the compact
  // list-card 9px rating digit the 1px offsets are ~20% of the glyph's
  // own stroke width, making digits look doubled/smeared rather than
  // outlined. Blur radius scales with font size implicitly (glyphs are
  // bigger, but so is the visual weight of the same blur relative to
  // them), so one shadow recipe works at both sizes without a doubling
  // artifact.
  const overlayTextClass =
    "text-white [text-shadow:0_0_3px_rgba(0,0,0,.95),0_0_3px_rgba(0,0,0,.95),0_1px_2px_rgba(0,0,0,.85)]";
  const overlayTextMutedClass =
    "text-white/90 [text-shadow:0_0_2.5px_rgba(0,0,0,.95),0_0_2.5px_rgba(0,0,0,.95),0_1px_1px_rgba(0,0,0,.8)]";
  const overlayTextCompactClass =
    "text-white [text-shadow:0_0_1.5px_rgba(0,0,0,.95),0_0_1.5px_rgba(0,0,0,.95),0_1px_1px_rgba(0,0,0,.85)]";

  return (
    <div className={`relative ${sizeHookClass} ${widthClass}`} style={{ aspectRatio: cardAspect }}>
      {bgImage ? (
        <>
          <img
            src={bgImage}
            alt=""
            className="absolute inset-0 w-full h-full object-contain"
            referrerPolicy="no-referrer"
            onLoad={(e) => {
              const { naturalWidth, naturalHeight } = e.currentTarget;
              if (naturalWidth && naturalHeight) setCardAspect(naturalWidth / naturalHeight);
            }}
            onError={imageErrorFallback(bgImage)}
          />
          <img
            src={cutoutImage}
            alt={altText}
            className={
              cutoutType === "base"
                ? "absolute left-1/2 top-[12%] -translate-x-1/2 w-[62%] h-[62%] object-contain"
                // Full-bleed cutouts are meant to fill the whole card, same
                // as a real FUT card - object-contain letterboxes whenever
                // the photo's own aspect ratio doesn't exactly match the
                // frame's, and since not every FUT.GG cutout PNG is fully
                // transparent outside the subject, that letterbox gap can
                // render as a solid colour bar instead of blending into the
                // bgImage layer beneath it. object-cover crops to fill
                // instead of padding, which is both the visually-correct
                // full-bleed treatment and immune to this.
                : "absolute inset-0 w-full h-full object-cover"
            }
            referrerPolicy="no-referrer"
            onError={imageErrorFallback(cutoutImage)}
          />

          {compact ? (
            <div className="absolute top-[10%] left-1/2 -translate-x-1/2 leading-none">
              <span className={`text-[9px] font-extrabold ${overlayTextCompactClass}`}>{rating}</span>
            </div>
          ) : (
            <>
              <div className="absolute top-[22%] left-[18%] flex flex-col items-center leading-none">
                <span className={`text-xl font-extrabold ${overlayTextClass}`}>{rating}</span>
                <span className={`text-[10px] font-bold mt-0.5 ${overlayTextClass}`}>{position}</span>
              </div>

              <div className="absolute bottom-[13%] inset-x-0 px-7">
                <div className={`text-center text-sm font-bold truncate mb-0.5 ${overlayTextClass}`}>
                  {name}
                </div>
                {showStats && (
                  <>
                    <div className="grid grid-cols-6 gap-0.5 mb-0.5">
                      {[
                        ["PAC", stats?.pace],
                        ["SHO", stats?.shooting],
                        ["PAS", stats?.passing],
                        ["DRI", stats?.dribbling],
                        ["DEF", stats?.defending],
                        ["PHY", stats?.physicality],
                      ].map(([label, value]) => (
                        <div key={label} className="text-center leading-tight">
                          <div className={`text-[10px] font-extrabold ${overlayTextClass}`}>
                            {value || "-"}
                          </div>
                          <div className={`text-[6px] font-semibold ${overlayTextMutedClass}`}>{label}</div>
                        </div>
                      ))}
                    </div>
                    <div className="flex items-center justify-center gap-1">
                      {nationImage && (
                        <img src={nationImage} alt="" className="w-3 h-3 object-contain" referrerPolicy="no-referrer" onError={imageErrorFallback(nationImage)} />
                      )}
                      {leagueImage && (
                        <img src={leagueImage} alt="" className="w-3 h-3 object-contain" referrerPolicy="no-referrer" onError={imageErrorFallback(leagueImage)} />
                      )}
                      {clubImage && (
                        <img src={clubImage} alt="" className="w-3 h-3 object-contain" referrerPolicy="no-referrer" onError={imageErrorFallback(clubImage)} />
                      )}
                    </div>
                  </>
                )}
              </div>
            </>
          )}
        </>
      ) : (
        <>
          <img
            src={fallbackImage}
            alt={altText}
            className="absolute inset-0 w-full h-full object-contain"
            referrerPolicy="no-referrer"
            onError={imageErrorFallback(fallbackImage)}
          />
          {rating != null && (
            <div className="absolute top-1 left-1 bg-black/70 text-white leading-none px-1 py-0.5 rounded text-[9px] font-extrabold">
              {rating}
            </div>
          )}
          {!compact && name && (
            <div className="absolute bottom-0 inset-x-0 bg-black/70 text-white text-center text-xs font-bold truncate px-1 py-1">
              {name}
            </div>
          )}
        </>
      )}
      {versionLabel && (
        <div className="absolute top-2 right-2 bg-black/70 text-white px-2 py-1 rounded text-xs">
          {versionLabel}
        </div>
      )}
    </div>
  );
}
