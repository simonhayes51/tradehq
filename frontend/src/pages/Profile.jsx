import React from "react";
import { useDashboard } from "../context/DashboardContext";

const Profile = () => {
  const { profile, isLoading, error } = useDashboard();

  if (isLoading) {
    return <p className="text-gray-400">Loading trader profile...</p>;
  }

  if (error) {
    return <p className="text-red-400">Error loading profile: {error}</p>;
  }

  if (!profile) {
    return <p className="text-gray-400">No trading data found yet.</p>;
  }

  return (
    <div className="bg-zinc-900 p-6 rounded-2xl shadow-md">
      <h1 className="text-2xl font-bold mb-4">Trader Profile</h1>
      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-black/40 p-4 rounded-xl">
          <h2 className="text-lg font-semibold">Total Profit</h2>
          <p className="text-3xl font-bold text-green-400">{profile.totalProfit?.toLocaleString() || '0'}</p>
        </div>
        <div className="bg-black/40 p-4 rounded-xl">
          <h2 className="text-lg font-semibold">Trades Logged</h2>
          <p className="text-3xl font-bold text-blue-400">{profile.tradesLogged || 0}</p>
        </div>
        <div className="bg-black/40 p-4 rounded-xl">
          <h2 className="text-lg font-semibold">Win Rate</h2>
          <p className="text-3xl font-bold text-green-400">{profile.winRate || 0}%</p>
        </div>
        <div className="bg-black/40 p-4 rounded-xl">
          <h2 className="text-lg font-semibold">Most Used Tag</h2>
          <p className="text-3xl font-bold text-purple-400">{profile.mostUsedTag || 'N/A'}</p>
        </div>
        {profile.bestTrade && (
          <div className="bg-black/40 p-4 rounded-xl md:col-span-2">
            <h2 className="text-lg font-semibold">Best Trade</h2>
            <p className="text-xl font-bold text-green-400">
              {profile.bestTrade.player} ({profile.bestTrade.version}) → +{profile.bestTrade.profit?.toLocaleString() || 'N/A'} coins
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default Profile;
