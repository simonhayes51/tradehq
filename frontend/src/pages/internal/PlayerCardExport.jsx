// src/pages/internal/PlayerCardExport.jsx
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import api from "../../axios";
import PlayerCardExportArt from "../../components/PlayerCardExportArt";

const EXPORT_WIDTH = 252;
const EXPORT_HEIGHT = 355;
const IMAGE_SETTLE_TIMEOUT_MS = 8000;

export default function PlayerCardExport() {
  const { cardId } = useParams();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const [data, setData] = useState(null);
  const [fetchError, setFetchError] = useState(null);

  useEffect(() => {
    const shell = document.getElementById("app-shell-bg");
    const previous = {
      htmlBackground: document.documentElement.style.background,
      htmlOverflow: document.documentElement.style.overflow,
      bodyBackground: document.body.style.background,
      bodyMargin: document.body.style.margin,
      bodyOverflow: document.body.style.overflow,
      shellBackground: shell?.style.background,
    };

    document.documentElement.style.background = "transparent";
    document.documentElement.style.overflow = "hidden";
    document.body.style.background = "transparent";
    document.body.style.margin = "0";
    document.body.style.overflow = "hidden";
    if (shell) shell.style.background = "transparent";

    const style = document.createElement("style");
    style.textContent = `
      *, *::before, *::after { animation:none!important; transition:none!important; box-sizing:border-box!important; }
      html, body, #root, #app-shell-bg {
        width:${EXPORT_WIDTH}px!important; height:${EXPORT_HEIGHT}px!important;
        min-width:0!important; min-height:0!important; margin:0!important; padding:0!important;
        overflow:hidden!important; background:transparent!important;
      }
    `;
    document.head.appendChild(style);

    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);

    return () => {
      document.documentElement.style.background = previous.htmlBackground;
      document.documentElement.style.overflow = previous.htmlOverflow;
      document.body.style.background = previous.bodyBackground;
      document.body.style.margin = previous.bodyMargin;
      document.body.style.overflow = previous.bodyOverflow;
      if (shell) shell.style.background = previous.shellBackground;
      style.remove();
      meta.remove();
      delete document.documentElement.dataset.cardReady;
      delete document.documentElement.dataset.cardDegraded;
      delete document.documentElement.dataset.cardDegradedLayers;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!cardId || !token) {
      setFetchError("missing card id or token");
      return undefined;
    }

    api.get(`/api/internal/render/player-card/${cardId}`, {
      params: { token },
      __skipAuthRedirect: true,
      __noRetry: true,
    }).then((response) => {
      if (!cancelled) setData(response.data?.data || null);
    }).catch((error) => {
      if (!cancelled) setFetchError(error?.userMessage || "failed to load card data");
    });

    return () => { cancelled = true; };
  }, [cardId, token]);

  useEffect(() => {
    if (!data) return undefined;
    let cancelled = false;

    // Wait on an individual <img> until it either loads or errors, and
    // report which outcome happened - a timeout race (as used previously)
    // can't distinguish "still pending" from "already 404'd", so a failed
    // required layer would silently be reported as ready. Resolves
    // immediately (as "loaded") for images that are already complete.
    function trackImage(image) {
      if (!image) return Promise.resolve({ ok: false, settled: false });
      if (image.complete) {
        // A completed <img> with naturalWidth 0 means the browser already
        // gave up on it (404/decode failure) before we attached listeners.
        return Promise.resolve({ ok: image.naturalWidth > 0, settled: true });
      }
      return new Promise((resolve) => {
        image.addEventListener("load", () => resolve({ ok: true, settled: true }), { once: true });
        image.addEventListener("error", () => resolve({ ok: false, settled: true }), { once: true });
      });
    }

    async function markReady() {
      await Promise.race([
        document.fonts?.ready ?? Promise.resolve(),
        new Promise((resolve) => setTimeout(resolve, IMAGE_SETTLE_TIMEOUT_MS)),
      ]);
      if (cancelled) return;

      const container = document.querySelector("[data-player-card-export]");

      // No bgImage means PlayerCardExportArt fell back to its `!bgImage`
      // branch, which renders nothing but the raw headshot stretched to the
      // full card frame - no background, no cutout, no rating/name/stats.
      // That is never a valid generated card (it's the exact "just a face
      // photo" bug), so treat missing card-art data as degraded outright,
      // regardless of whether the fallback photo itself loads fine, rather
      // than waiting on image-load tracking below.
      if (!data.bgImage) {
        document.documentElement.dataset.cardDegraded = "true";
        document.documentElement.dataset.cardDegradedLayers = "missing-card-art";
        await new Promise((resolve) => requestAnimationFrame(resolve));
        await new Promise((resolve) => requestAnimationFrame(resolve));
        if (!cancelled) document.documentElement.dataset.cardReady = "true";
        return;
      }

      // The "must load for a valid card" layers: the card-frame background
      // and the cutout/player artwork. Everything else (nation/league/club
      // crests) is decorative - losing one shouldn't fail the whole render.
      const requiredLayers = [
        ["bg", container?.querySelector("[data-card-background]")],
        ...(data.cutoutImage ? [["cutout", container?.querySelector("[data-card-player]")]] : []),
      ];

      const allImages = container ? Array.from(container.querySelectorAll("img")) : [];
      const requiredEls = new Set(requiredLayers.map(([, el]) => el).filter(Boolean));

      // Track every image (so decorative ones still get a chance to
      // decode/settle), but only required layers gate the "degraded" flag,
      // and a required layer that fires onError counts as failed the
      // instant it happens rather than waiting out the full timeout.
      const results = new Map();
      const trackers = allImages.map(async (image) => {
        const result = await trackImage(image);
        results.set(image, result);
        if (result.ok && typeof image.decode === "function") {
          try { await image.decode(); } catch { /* already decoded */ }
        }
      });

      await Promise.race([
        Promise.all(trackers),
        new Promise((resolve) => setTimeout(resolve, IMAGE_SETTLE_TIMEOUT_MS)),
      ]);
      if (cancelled) return;

      const failedLayers = requiredLayers
        .filter(([, el]) => {
          if (!el) return true; // required layer never even rendered
          const result = results.get(el);
          return !result || !result.settled || !result.ok;
        })
        .map(([layer]) => layer);

      if (failedLayers.length > 0) {
        container?.setAttribute("data-card-export-error", "bg-image-failed");
        document.documentElement.dataset.cardDegraded = "true";
        document.documentElement.dataset.cardDegradedLayers = failedLayers.join(",");
      }

      await new Promise((resolve) => requestAnimationFrame(resolve));
      await new Promise((resolve) => requestAnimationFrame(resolve));
      if (!cancelled) document.documentElement.dataset.cardReady = "true";
    }

    markReady();
    return () => { cancelled = true; };
  }, [data]);

  if (fetchError) return <div data-card-fetch-error={fetchError} />;
  if (!data) return null;

  return <PlayerCardExportArt
    width={EXPORT_WIDTH}
    height={EXPORT_HEIGHT}
    bgImage={data.bgImage}
    cardColor={data.cardColor}
    cutoutImage={data.cutoutImage}
    cutoutType={data.cutoutType || "special"}
    versionLabel={data.versionLabel}
    fallbackImage={data.fallbackImage}
    rating={data.rating}
    position={data.position}
    name={data.displayName || data.name}
    altText={data.name}
    stats={data.stats}
    nationImage={data.nationImage}
    leagueImage={data.leagueImage}
    clubImage={data.clubImage}
  />;
}
