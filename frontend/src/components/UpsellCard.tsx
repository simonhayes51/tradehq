import React from "react";
import { Link } from "react-router-dom";

type Props = {
  title?: string;
  message?: string;
  from?: string;
};

const UpsellCard: React.FC<Props> = ({
  title = "This feature is Premium",
  message = "Upgrade to unlock Smart Buy, Smart Trending, advanced alerts, and more.",
  from,
}) => {
  const to = from ? `/billing?from=${encodeURIComponent(from)}` : "/billing";
  return (
    <div className="rounded-2xl border p-6 shadow-sm bg-white/50 dark:bg-zinc-900/50">
      <h3 className="text-lg font-semibold mb-2">{title}</h3>
      <p className="text-sm opacity-80 mb-4">{message}</p>
      <div className="flex gap-3">
        <Link
          to={to}
          className="inline-flex items-center rounded-xl px-4 py-2 border bg-black text-white dark:bg-white dark:text-black hover:opacity-90"
        >
          Upgrade
        </Link>
        <Link
          to="/pricing"
          className="inline-flex items-center rounded-xl px-4 py-2 border hover:bg-black/5 dark:hover:bg-white/10"
        >
          See plans
        </Link>
      </div>
    </div>
  );
};

export default UpsellCard;
