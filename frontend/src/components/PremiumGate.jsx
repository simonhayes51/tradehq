// src/components/PremiumGate.jsx - COMPLETE FIXED VERSION
import React from "react";
import { Crown } from "lucide-react";
import { useEntitlements } from "../context/EntitlementsContext";

/**
 * Blocks the entire widget when locked (children are NOT rendered).
 * Use for hard paid widgets.
 */
export default function PremiumGate({
  feature,            // e.g. "advanced_analytics"
  featureName,        // label, e.g. "Advanced Analytics"
  className = "",     // pass your card rounding if needed
  children,           // ignored when locked
  fallback,           // fallback content to show instead of the upgrade prompt
}) {
  const { isPremium, features } = useEntitlements();

  // `isPremium` is the legacy entitlement field name; the customer-facing
  // paid plan is Elite.
  const hasAccess = isPremium || (feature && features.includes(feature));

  if (hasAccess) return <>{children}</>;

  // If fallback is provided, show that instead of the upgrade prompt
  if (fallback) return <>{fallback}</>;

  return (
    <div className={`bg-gray-900/70 rounded-2xl p-4 border border-gray-800 h-[150px] flex flex-col justify-between ${className}`}>
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="inline-flex items-center gap-2 text-yellow-300 font-semibold mb-1">
            <Crown size={14} /> Elite Feature
          </div>
          <div className="text-xs text-gray-300">
            {featureName || "This widget"} is available on the Elite plan.
          </div>
        </div>
      </div>
      <div className="flex justify-center">
        <a
          href="/billing"
          className="inline-flex items-center justify-center px-3 py-1.5 rounded-lg bg-gradient-to-r from-yellow-500 to-amber-500 text-black text-xs font-semibold hover:from-yellow-400 hover:to-amber-400 transition-colors"
        >
          Upgrade Now
        </a>
      </div>
    </div>
  );
}
