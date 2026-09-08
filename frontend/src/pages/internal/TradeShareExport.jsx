// src/pages/internal/TradeShareExport.jsx
//
// Hidden render-only route for the Portfolio "Share" button - headless
// Chromium (app/services/trade_share_render.py) navigates here with a
// signed token, waits for data-trade-share-ready, then screenshots
// [data-trade-share-export]. Mirrors PlayerCardExport.jsx's contract but
// far simpler: at most one thumbnail image to track, not a layered
// card-frame/cutout system.
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import api from "../../axios";
import TradeShareCardArt from "../../components/TradeShareCalledItArt";

const IMAGE_SETTLE_TIMEOUT_MS = 6000;

export default function TradeShareExport() {
  const { tradeId } = useParams();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const format = searchParams.get("format") === "portrait" ? "portrait" : "wide";
  const [data, setData] = useState(null);
  const [fetchError, setFetchError] = useState(null);

  useEffect(() => {
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    document.body.style.margin = "0";
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => {
      meta.remove();
      delete document.documentElement.dataset.tradeShareReady;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!tradeId || !token) {
      setFetchError("missing trade id or token");
      return undefined;
    }
    api.get(`/api/internal/render/trade-share/${tradeId}`, {
      params: { token },
      __skipAuthRedirect: true,
      __noRetry: true,
    }).then((response) => {
      if (!cancelled) setData(response.data?.data || null);
    }).catch((error) => {
      if (!cancelled) setFetchError(error?.userMessage || "failed to load trade data");
    });
    return () => { cancelled = true; };
  }, [tradeId, token]);

  useEffect(() => {
    if (!data && !fetchError) return undefined;
    let cancelled = false;

    async function markReady() {
      if (fetchError) {
        document.documentElement.dataset.tradeShareReady = "true";
        return;
      }
      const thumb = document.querySelector("[data-trade-share-thumb]");
      const imageSettled = thumb
        ? Promise.race([
            new Promise((resolve) => {
              if (thumb.complete && thumb.naturalWidth > 0) return resolve();
              thumb.addEventListener("load", () => resolve(), { once: true });
            }),
            new Promise((resolve) => setTimeout(resolve, IMAGE_SETTLE_TIMEOUT_MS)),
          ])
        : Promise.resolve();

      await Promise.all([document.fonts?.ready ?? Promise.resolve(), imageSettled]);
      if (cancelled) return;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      if (!cancelled) document.documentElement.dataset.tradeShareReady = "true";
    }

    markReady();
    return () => { cancelled = true; };
  }, [data, fetchError]);

  if (fetchError) {
    const size = format === "portrait" ? { width: 1080, height: 1350 } : { width: 900, height: 470 };
    return (
      <div data-trade-share-export data-trade-share-error={fetchError} style={size} />
    );
  }
  if (!data) return null;

  return <TradeShareCardArt data={data} format={format} />;
}
