import React from "react";
import { useSettings } from "../context/SettingsContext";

const ALL_WIDGETS = [
  { key: "profit", label: "Net Profit" },
  { key: "tax", label: "EA Tax Paid" },
  { key: "balance", label: "Starting Balance" },
  { key: "trades", label: "Total Trades" },
  { key: "profit_trend", label: "Profit Trend (7D)" },
  { key: "winrate", label: "Win Rate" },
  { key: "avg_profit", label: "Average Profit / Trade" },
  { key: "best_trade", label: "Best Trade" },
  { key: "volume", label: "Coin Volume" },
  { key: "latest_trade", label: "Latest Trade" },
  { key: "top_earner", label: "Top Earner" },
  { key: "promo", label: "Next Promo" },
  { key: "trending", label: "Trending (6h)" },
  { key: "alerts", label: "Watchlist Alerts" },
];

export default function SettingsWidgets() {
  const {
    include_tax_in_profit,
    visible_widgets,
    recent_trades_limit,
    alerts,
    saveSettings,
  } = useSettings();

  const toggleWidget = (key) => {
    const on = visible_widgets.includes(key);
    const next = on ? visible_widgets.filter((w) => w !== key) : [...visible_widgets, key];
    saveSettings({ visible_widgets: next });
  };

  const resetDefaults = () => {
    localStorage.removeItem("user_settings");
    localStorage.removeItem("alerts_settings");
    window.location.reload();
  };

  return (
    <div id="widgets" className="space-y-10">
      {/* Profit calc */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Profit Calculation</h2>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={include_tax_in_profit}
            onChange={(e) => saveSettings({ include_tax_in_profit: e.target.checked })}
          />
          <span className="text-gray-300">Include EA Tax in profit numbers</span>
        </label>
      </section>

      {/* Recent trades limit */}
      <section>
        <h2 className="text-lg font-semibold mb-2">Recent Trades Limit</h2>
        <select
          className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-gray-200"
          value={recent_trades_limit}
          onChange={(e) => saveSettings({ recent_trades_limit: Number(e.target.value) })}
        >
          <option value={5}>Show last 5</option>
          <option value={10}>Show last 10</option>
          <option value={20}>Show last 20</option>
        </select>
      </section>

      {/* Widget toggles */}
      <section>
        <h2 className="text-lg font-semibold mb-4">Visible Widgets</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {ALL_WIDGETS.map((w) => (
            <label
              key={w.key}
              className="flex items-center gap-3 bg-gray-900 rounded-lg p-3 border border-gray-700 hover:border-gray-600 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={visible_widgets.includes(w.key)}
                onChange={() => toggleWidget(w.key)}
              />
              <span className="text-gray-200">{w.label}</span>
            </label>
          ))}
        </div>
      </section>

      {/* Watchlist Alerts */}
      <section id="alerts">
        <h2 className="text-lg font-semibold mb-2">Watchlist Alerts</h2>

        <div className="bg-gray-900 rounded-xl border border-gray-700 p-4 space-y-4">
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={alerts.enabled}
              onChange={(e) => saveSettings({ alerts: { enabled: e.target.checked } })}
            />
            <span className="text-gray-200">Enable alerts</span>
          </label>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <label className="flex flex-col text-sm">
              <span className="text-gray-400 mb-1">Threshold (absolute % move)</span>
              <input
                type="number"
                min={1} max={50} step={0.5}
                className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-gray-200"
                value={alerts.thresholdPct}
                onChange={(e) => saveSettings({ alerts: { thresholdPct: Math.max(1, Math.min(50, Number(e.target.value) || 0)) } })}
                disabled={!alerts.enabled}
              />
            </label>

            <label className="flex flex-col text-sm">
              <span className="text-gray-400 mb-1">Cooldown (minutes)</span>
              <input
                type="number"
                min={5} max={180}
                className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-gray-200"
                value={alerts.cooldownMin}
                onChange={(e) => saveSettings({ alerts: { cooldownMin: Math.max(5, Math.min(180, Number(e.target.value) || 0)) } })}
                disabled={!alerts.enabled}
              />
            </label>

            <label className="flex flex-col text-sm">
              <span className="text-gray-400 mb-1">Delivery</span>
              <select
                className="bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-gray-200"
                value={alerts.delivery}
                onChange={(e) => saveSettings({ alerts: { delivery: e.target.value } })}
                disabled={!alerts.enabled}
              >
                <option value="inapp">In-app</option>
                <option value="discord">Discord DM</option>
              </select>
            </label>
          </div>

          <p className="text-xs text-gray-500">
            Alerts trigger when a watchlist item’s <em>change %</em> meets your threshold. “Discord DM” requires your bot token & guild setup.
          </p>
        </div>
      </section>

      <button
        onClick={resetDefaults}
        className="text-sm px-3 py-2 rounded-lg bg-gray-900 border border-gray-700 hover:border-gray-600"
      >
        Reset to defaults
      </button>
    </div>
  );
}
