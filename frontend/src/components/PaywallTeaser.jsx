// src/components/PaywallTeaser.jsx
// Conversion pattern: show the value, blur the precision.
// Wrap any premium data block; free users see a frosted preview + hype CTA,
// paid users see children untouched.
import React from "react";
import { Link } from "react-router-dom";
import { useEntitlements } from "../context/EntitlementsContext";

const LIME = "#91db32";

export default function PaywallTeaser({
  feature,
  title = "This one's for the Pros",
  pitch = "Real numbers. Real sold prices. Real edge.",
  children,
}) {
  const { features } = useEntitlements() || {};
  const unlocked = Array.isArray(features) && features.includes(feature);

  if (unlocked) return children;

  return (
    <div className="relative rounded-2xl overflow-hidden border border-gray-800">
      {/* Blurred, non-interactive preview of the real content */}
      <div className="pointer-events-none select-none blur-md opacity-60" aria-hidden>
        {children}
      </div>

      <div className="absolute inset-0 grid place-items-center bg-black/50 backdrop-blur-[2px] p-4">
        <div className="text-center max-w-xs">
          <div
            className="mx-auto mb-3 w-10 h-10 grid place-items-center rounded-full text-lg"
            style={{ background: "rgba(145,219,50,0.15)", color: LIME }}
          >
            ⚡
          </div>
          <p className="text-sm font-extrabold text-white leading-snug">{title}</p>
          <p className="mt-1 text-xs text-gray-300">{pitch}</p>
          <Link
            to="/billing"
            className="mt-3 inline-block px-4 py-2 rounded-xl text-xs font-bold text-black active:scale-[0.98] transition"
            style={{ background: LIME }}
          >
            Unlock Pro →
          </Link>
          <p className="mt-2 text-[10px] text-gray-500">Cancel anytime. No cap.</p>
        </div>
      </div>
    </div>
  );
}
