// src/pages/internal/TradeSummaryExport.jsx
//
// Hidden render-only route for the Portfolio period-summary Share button
// (Today/This Week/All-Time) - mirrors TradeShareExport.jsx's readiness
// contract, best-trade thumbnail included: this card was originally
// text/stat-only, but TradeSummaryCardArt now renders a real card-art
// thumbnail next to the best-trade line, and the screenshot has to wait
// for that image to settle (load or fail) the same way the trade-result
// card's thumbnail does - otherwise Playwright can screenshot before the
// image has even started loading, producing a blank thumbnail in the
// exported PNG regardless of whether the image itself was ever going to
// succeed.
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import api from "../../axios";
import TradeSummaryCardArt from "../../components/TradeSummaryCardArt";

const IMAGE_SETTLE_TIMEOUT_MS = 6000;

export default function TradeSummaryExport() {
  const { period } = useParams();
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
      delete document.documentElement.dataset.tradeSummaryReady;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!period || !token) {
      setFetchError("missing period or token");
      return undefined;
    }
    api.get(`/api/internal/render/trade-summary/${period}`, {
      params: { token },
      __skipAuthRedirect: true,
      __noRetry: true,
    }).then((response) => {
      if (!cancelled) setData(response.data?.data || null);
    }).catch((error) => {
      if (!cancelled) setFetchError(error?.userMessage || "failed to load summary data");
    });
    return () => { cancelled = true; };
  }, [period, token]);

  useEffect(() => {
    if (!data && !fetchError) return undefined;
    let cancelled = false;

    async function markReady() {
      if (fetchError) {
        document.documentElement.dataset.tradeSummaryReady = "true";
        return;
      }
      const thumb = document.querySelector("[data-trade-summary-thumb]");
      // Only "load" counts as settled, not "error" - see the identical
      // comment in TradeShareExport.jsx. BestTradeThumb swaps a failed
      // remote image to the local placeholder itself, which then fires
      // its own fresh load/error cycle; the timeout is the backstop.
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
      if (!cancelled) document.documentElement.dataset.tradeSummaryReady = "true";
    }

    markReady();
    return () => { cancelled = true; };
  }, [data, fetchError]);

  if (fetchError) {
    const size = format === "portrait" ? { width: 1080, height: 1350 } : { width: 900, height: 470 };
    return (
      <div data-trade-summary-export data-trade-summary-error={fetchError} style={size} />
    );
  }
  if (!data) return null;

  return <TradeSummaryCardArt data={data} format={format} />;
}
