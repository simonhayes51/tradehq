// src/components/PremiumRoute.tsx
import React from "react";
import { Outlet } from "react-router-dom";
import { useEntitlements } from "../context/EntitlementsContext";
import PremiumGate from "./PremiumGate";

type Props = {
  /** Premium feature requirement - "any" means any premium; or pass a specific backend feature key */
  feature?:
    | "smart_buy"
    | "trade_finder"
    | "deal_confidence"
    | "backtest"
    | "smart_trending"
    | "portfolio_optimizer"
    | "ai_copilot"
    | "market_sentiment"
    | "market_maker"
    | "advanced_analytics"
    | "any";
  /** Human-readable name for the feature (e.g. "Smart Buy AI", "Advanced Market Trends") */
  featureName?: string;
  /** Child components to render if access is granted */
  children?: React.ReactNode;
};

export default function PremiumRoute({ 
  feature = "any", 
  featureName,
  children 
}: Props) {
  const { loading, isPremium, features } = useEntitlements();

  // Show loading state while checking entitlements
  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div>
      </div>
    );
  }

  // Check if user has access to this feature
  const hasAccess = feature === "any" ? isPremium : features.includes(feature as any);

  // If no access, show the premium required page
  if (!hasAccess) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <PremiumGate
          feature={feature !== "any" ? (feature as any) : undefined}
          featureName={featureName}
          className="max-w-md w-full"
        />
      </div>
    );
  }

  // User has access - render the protected content
  return <>{children ?? <Outlet />}</>;
}
