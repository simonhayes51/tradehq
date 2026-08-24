// src/pages/internal/TradeSummaryExport.jsx
//
// Hidden render-only route for the Portfolio period-summary Share button
// (Today/This Week/All-Time) - mirrors TradeShareExport.jsx exactly, but
// has no thumbnail to track since the summary card art is text/stat-only.
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import api from "../../axios";
import TradeSummaryCardArt from "../../components/TradeSummaryCardArt";

export default function TradeSummaryExport() {
  const { period } = useParams();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
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
    (async () => {
      if (!fetchError) await (document.fonts?.ready ?? Promise.resolve());
      if (cancelled) return;
      await new Promise((resolve) => requestAnimationFrame(resolve));
      if (!cancelled) document.documentElement.dataset.tradeSummaryReady = "true";
    })();
    return () => { cancelled = true; };
  }, [data, fetchError]);

  if (fetchError) {
    return (
      <div data-trade-summary-export data-trade-summary-error={fetchError} style={{ width: 900, height: 470 }} />
    );
  }
  if (!data) return null;

  return <TradeSummaryCardArt data={data} />;
}
