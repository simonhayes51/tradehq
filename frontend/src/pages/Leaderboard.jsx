import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Trophy, TrendingUp, Medal, Crown, Share2, Eye, Lock } from 'lucide-react';
import api from '../axios';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';

const Leaderboard = () => {
  const [activeTab, setActiveTab] = useState('weekly');
  const [leaderboardData, setLeaderboardData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [userRank, setUserRank] = useState(null);
  const { user } = useAuth();

  const tabs = [
    { id: 'weekly', label: 'This Week', icon: '📅' },
    { id: 'monthly', label: 'This Month', icon: '📆' },
    { id: 'alltime', label: 'All Time', icon: '♾️' }
  ];

  useEffect(() => {
    fetchLeaderboard();
  }, [activeTab]);

  const fetchLeaderboard = async () => {
    setLoading(true);
    try {
      const { data } = await api.get(`/api/leaderboard/${activeTab}`);
      const rankings = data.rankings || data.leaderboard || [];
      const userRankData = data.userRank || data.your_rank || null;
      setLeaderboardData(rankings);
      setUserRank(userRankData);
    } catch (error) {
      console.error('Leaderboard error:', error);
      toast.error(error.response?.data?.detail || error.message || 'Failed to fetch leaderboard');
    } finally {
      setLoading(false);
    }
  };

  const getRankIcon = (rank) => {
    if (rank === 1) return <Crown className="w-6 h-6 text-yellow-400" />;
    if (rank === 2) return <Medal className="w-6 h-6 text-slate-300" />;
    if (rank === 3) return <Medal className="w-6 h-6 text-orange-400" />;
    return <span className="text-slate-400 font-bold">#{rank}</span>;
  };

  const getRankBg = (rank) => {
    if (rank === 1) return 'bg-gradient-to-r from-yellow-600/20 to-yellow-500/10 border-yellow-500/30';
    if (rank === 2) return 'bg-gradient-to-r from-slate-600/20 to-slate-500/10 border-slate-400/30';
    if (rank === 3) return 'bg-gradient-to-r from-orange-600/20 to-orange-500/10 border-orange-500/30';
    return 'bg-slate-800/30 border-white/5';
  };

  const shareRank = async (rank) => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'FUT Trader Hub Leaderboard',
          text: `I'm ranked #${rank} on the FUT Trader Hub leaderboard! 🏆`,
          url: window.location.href
        });
      } catch (error) {
        // User cancelled share
      }
    } else {
      navigator.clipboard.writeText(`I'm ranked #${rank} on FUT Trader Hub! 🏆`);
      toast.success('Copied to clipboard!');
    }
  };

  return (
    <div className="min-h-screen bg-[#0e1320] text-white p-6">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-8"
        >
          <div className="flex items-center gap-3 mb-2">
            <Trophy className="w-8 h-8 text-yellow-400" />
            <h1 className="text-4xl font-black">Leaderboard</h1>
          </div>
          <p className="text-slate-400">
            Compete with traders worldwide • Climb the ranks • Earn bragging rights
          </p>
        </motion.div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6 bg-slate-900/50 p-2 rounded-xl border border-white/10">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 px-4 py-3 rounded-lg font-bold transition-all ${
                activeTab === tab.id
                  ? 'bg-gradient-to-r from-purple-600 to-purple-500 text-white shadow-lg'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800/50'
              }`}
            >
              <span className="mr-2">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Your Rank Card */}
        {userRank && (
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className="mb-6 bg-gradient-to-r from-purple-900/40 to-purple-800/20 rounded-2xl p-6 border border-purple-500/30"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4">
                <div className="w-16 h-16 bg-gradient-to-br from-purple-600 to-purple-400 rounded-xl flex items-center justify-center">
                  {getRankIcon(userRank.rank)}
                </div>
                <div>
                  <p className="text-sm text-slate-400">Your Rank</p>
                  <h3 className="text-2xl font-black">#{userRank.rank}</h3>
                  <p className="text-sm text-slate-400">
                    {userRank.profit >= 0 ? '+' : ''}{userRank.profit.toLocaleString()} coins
                  </p>
                </div>
              </div>
              <button
                onClick={() => shareRank(userRank.rank)}
                className="bg-slate-800/50 hover:bg-slate-700/50 rounded-xl px-4 py-2 border border-white/10 transition-all"
              >
                <Share2 className="w-5 h-5" />
              </button>
            </div>
          </motion.div>
        )}

        {/* Leaderboard */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="bg-slate-900/50 rounded-2xl border border-white/10 overflow-hidden"
        >
          <div className="p-6 border-b border-white/10">
            <h2 className="text-xl font-bold">Top Traders</h2>
          </div>

          {loading ? (
            <div className="p-12 text-center">
              <div className="w-12 h-12 border-4 border-purple-600/30 border-t-purple-600 rounded-full animate-spin mx-auto mb-4" />
              <p className="text-slate-400">Loading rankings...</p>
            </div>
          ) : (
            <div className="divide-y divide-white/5">
              {leaderboardData.map((trader, idx) => (
                <motion.div
                  key={trader.userId}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: idx * 0.05 }}
                  className={`p-4 flex items-center gap-4 ${getRankBg(trader.rank)} border-l-4 ${
                    trader.userId === user?.id ? 'ring-2 ring-purple-500/50' : ''
                  }`}
                >
                  {/* Rank */}
                  <div className="w-12 flex items-center justify-center">
                    {getRankIcon(trader.rank)}
                  </div>

                  {/* Avatar */}
                  <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-slate-700 to-slate-600 flex items-center justify-center font-bold text-lg">
                    {trader.username?.charAt(0).toUpperCase() || '?'}
                  </div>

                  {/* User Info */}
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="font-bold">
                        {trader.isAnonymous ? (
                          <span className="flex items-center gap-1 text-slate-400">
                            <Eye className="w-4 h-4" />
                            Hidden Trader
                          </span>
                        ) : (
                          trader.username
                        )}
                      </h3>
                      {trader.isPremium && (
                        <span className="px-2 py-0.5 bg-purple-500/20 text-purple-400 text-xs font-bold rounded">
                          PRO
                        </span>
                      )}
                      {trader.userId === user?.id && (
                        <span className="px-2 py-0.5 bg-blue-500/20 text-blue-400 text-xs font-bold rounded">
                          YOU
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-slate-400">
                      {trader.totalTrades} trades • {trader.winRate}% win rate
                    </p>
                  </div>

                  {/* Stats */}
                  <div className="text-right">
                    <p className={`text-xl font-black ${
                      trader.profit >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      {trader.profit >= 0 ? '+' : ''}{trader.profit.toLocaleString()}
                    </p>
                    <p className="text-sm text-slate-400">
                      {trader.roi >= 0 ? '+' : ''}{trader.roi.toFixed(1)}% ROI
                    </p>
                  </div>

                  {/* Arrow */}
                  {trader.rankChange !== 0 && (
                    <div className={`${
                      trader.rankChange > 0 ? 'text-green-400' : 'text-red-400'
                    }`}>
                      <TrendingUp className={`w-5 h-5 ${
                        trader.rankChange < 0 ? 'rotate-180' : ''
                      }`} />
                    </div>
                  )}
                </motion.div>
              ))}
            </div>
          )}
        </motion.div>

        {/* Privacy Notice */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
          className="mt-6 bg-slate-900/30 border border-white/5 rounded-xl p-4"
        >
          <div className="flex gap-3">
            <Lock className="w-5 h-5 text-slate-400 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-slate-400">
              <p className="font-bold text-white mb-1">Privacy Settings</p>
              <p>
                You can hide your username from the public leaderboard in Settings.
                Your rank and stats will still be tracked, but displayed anonymously.
              </p>
            </div>
          </div>
        </motion.div>

        {/* Prizes Section */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
          className="mt-6 bg-gradient-to-r from-yellow-900/20 to-yellow-800/10 rounded-2xl p-6 border border-yellow-500/20"
        >
          <h3 className="text-xl font-bold mb-3 flex items-center gap-2">
            <Trophy className="w-6 h-6 text-yellow-400" />
            Monthly Prizes
          </h3>
          <div className="grid md:grid-cols-3 gap-4">
            <div className="bg-slate-900/50 rounded-xl p-4">
              <div className="text-3xl mb-2">🥇</div>
              <h4 className="font-bold mb-1">1st Place</h4>
              <p className="text-sm text-slate-400">6 months Premium + Discord badge</p>
            </div>
            <div className="bg-slate-900/50 rounded-xl p-4">
              <div className="text-3xl mb-2">🥈</div>
              <h4 className="font-bold mb-1">2nd Place</h4>
              <p className="text-sm text-slate-400">3 months Premium</p>
            </div>
            <div className="bg-slate-900/50 rounded-xl p-4">
              <div className="text-3xl mb-2">🥉</div>
              <h4 className="font-bold mb-1">3rd Place</h4>
              <p className="text-sm text-slate-400">1 month Premium</p>
            </div>
          </div>
        </motion.div>
      </div>
    </div>
  );
};

export default Leaderboard;
