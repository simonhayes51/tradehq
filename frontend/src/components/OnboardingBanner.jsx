// src/components/OnboardingBanner.jsx
// First-session checklist. New users used to land on a dashboard of "N/A"s;
// this gives them three concrete wins in the first two minutes.
// Dismiss state lives in localStorage; auto-hides once all steps are done.
import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";
import { useDashboard } from "../context/DashboardContext";

const KEY = "onboarding_dismissed_v1";
const LIME = "#91db32";

export default function OnboardingBanner() {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(KEY) === "1";
    } catch {
      return false;
    }
  });

  const dash = useDashboard() || {};
  const tradesLogged = dash?.profile?.tradesLogged ?? dash?.data?.profile?.tradesLogged ?? 0;

  const steps = useMemo(
    () => [
      {
        to: "/add-trade",
        label: "Log your first flip",
        sub: "Takes 20 seconds. Your P&L starts here.",
        done: tradesLogged > 0,
        emoji: "📒",
      },
      {
        to: "/watchlist",
        label: "Watch a card",
        sub: "Get pinged the moment it moves.",
        done: false,
        emoji: "👀",
      },
      {
        to: "/player-search",
        label: "Check a real price",
        sub: "Actual sold prices — not asking prices.",
        done: false,
        emoji: "🔎",
      },
    ],
    [tradesLogged]
  );

  useEffect(() => {
    if (steps.every((s) => s.done)) {
      try {
        localStorage.setItem(KEY, "1");
      } catch {}
      setDismissed(true);
    }
  }, [steps]);

  if (dismissed) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(KEY, "1");
    } catch {}
    setDismissed(true);
  };

  return (
    <div className="relative rounded-2xl border border-gray-800 bg-gray-900/70 p-4 mb-4">
      <button
        onClick={dismiss}
        aria-label="Dismiss onboarding"
        className="absolute top-3 right-3 text-gray-500 hover:text-gray-300"
      >
        <X size={16} />
      </button>
      <p className="text-sm font-extrabold text-white">
        Welcome in. <span style={{ color: LIME }}>3 quick wins</span> to get you trading smarter 👇
      </p>
      <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2">
        {steps.map((s) => (
          <Link
            key={s.to}
            to={s.to}
            className={`rounded-xl border p-3 transition active:scale-[0.98] ${
              s.done
                ? "border-transparent opacity-50 line-through"
                : "border-gray-800 hover:border-gray-600"
            }`}
          >
            <div className="text-lg" aria-hidden>
              {s.done ? "✅" : s.emoji}
            </div>
            <div className="text-xs font-bold text-white mt-1">{s.label}</div>
            <div className="text-[11px] text-gray-400">{s.sub}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
