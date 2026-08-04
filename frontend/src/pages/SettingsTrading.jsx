import React from "react";
import { useSettings } from "../context/SettingsContext";

export default function SettingsTrading() {
  const { default_platform, default_quantity, saveSettings } = useSettings();

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4">Trading Preferences</h2>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-300">Default Platform</span>
          <select
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-gray-200"
            value={default_platform}
            onChange={(e) => saveSettings({ default_platform: e.target.value })}
          >
            <option value="Console">Console</option>
            <option value="PlayStation">PlayStation</option>
            <option value="Xbox">Xbox</option>
            <option value="PC">PC</option>
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-300">Default Quantity</span>
          <input
            type="number"
            min={1}
            className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-gray-200"
            value={default_quantity}
            onChange={(e) => {
              const n = Math.max(1, Number(e.target.value) || 1);
              saveSettings({ default_quantity: n });
            }}
          />
        </label>
      </div>

      <p className="text-xs text-gray-500 mt-4">
        These preferences are stored locally in your browser.
      </p>
    </div>
  ); 
}