import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Gift, Users, Link as LinkIcon, Copy, Check, Award, TrendingUp } from 'lucide-react';
import api from '../axios';
import toast from 'react-hot-toast';
import { useAuth } from '../context/AuthContext';

const ReferralProgram = () => {
  const [referralData, setReferralData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const { user } = useAuth();

  useEffect(() => {
    fetchReferralData();
  }, []);

  const fetchReferralData = async () => {
    try {
      const { data } = await api.get('/api/referrals/stats');
      setReferralData(data);
    } catch (error) {
      toast.error(error.userMessage || 'Failed to fetch referral data');
    } finally {
      setLoading(false);
    }
  };

  // /api/referrals/stats returns { referral_code, stats: {...}, rewards: {...},
  // recent_referrals: [...], share_url }, not the camelCase shape this
  // component used to assume (referralData.code/.totalReferrals/etc were
  // always undefined against the real response - reading referral_code
  // directly fixes both the stats display and the "share a working link"
  // bug in one pass, since share_url is already fully formed server-side.
  const referralLink = referralData?.share_url || `https://app.futhub.co.uk?ref=${referralData?.referral_code || ''}`;

  const copyReferralLink = () => {
    navigator.clipboard.writeText(referralLink);
    setCopied(true);
    toast.success('Referral link copied!');
    setTimeout(() => setCopied(false), 2000);
  };

  const shareOnPlatform = (platform) => {
    const link = referralLink;
    const text = '🎮 Level up your FUT trading with AI-powered insights! Join with my link:';

    const urls = {
      twitter: `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(link)}`,
      discord: link, // Copy for Discord
      reddit: `https://reddit.com/submit?url=${encodeURIComponent(link)}&title=${encodeURIComponent(text)}`
    };

    if (platform === 'discord') {
      navigator.clipboard.writeText(`${text}\n${link}`);
      toast.success('Message copied! Paste in Discord');
    } else {
      window.open(urls[platform], '_blank');
    }
  };

  // Matches what /api/referrals/stats actually computes and grants today
  // (a flat coin reward per conversion) - this used to promise 7 days/1
  // month/lifetime Premium and a 30% recurring commission, none of which
  // any backend code grants, which is exactly the kind of broken promise
  // a trading-recommendation product can't afford sitting in production.
  const perConversion = referralData?.rewards?.per_conversion ?? 100;
  const rewards = [
    {
      icon: '🪙',
      title: `${perConversion.toLocaleString()} Coins`,
      description: 'Earned automatically the moment your friend signs up',
      requirement: 'Per successful referral'
    },
    {
      icon: '📈',
      title: 'Stacks Every Time',
      description: 'No cap - every friend who joins earns you more',
      requirement: 'Unlimited referrals'
    }
  ];

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0e1320] text-white flex items-center justify-center">
        <div className="w-12 h-12 border-4 border-purple-600/30 border-t-purple-600 rounded-full animate-spin" />
      </div>
    );
  }

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
            <Gift className="w-8 h-8 text-purple-400" />
            <h1 className="text-4xl font-black">Referral Program</h1>
          </div>
          <p className="text-slate-400">
            Share the wealth • Earn rewards • Grow the community
          </p>
        </motion.div>

        {/* Stats Overview */}
        <div className="grid md:grid-cols-4 gap-4 mb-8">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="bg-gradient-to-br from-purple-900/40 to-purple-800/20 rounded-2xl p-6 border border-purple-500/30"
          >
            <Users className="w-8 h-8 text-purple-400 mb-2" />
            <p className="text-sm text-slate-400 mb-1">Total Referrals</p>
            <h3 className="text-3xl font-black">{referralData?.stats?.conversions || 0}</h3>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="bg-gradient-to-br from-green-900/40 to-green-800/20 rounded-2xl p-6 border border-green-500/30"
          >
            <Award className="w-8 h-8 text-green-400 mb-2" />
            <p className="text-sm text-slate-400 mb-1">Coins Earned</p>
            <h3 className="text-3xl font-black">{(referralData?.rewards?.total_earned || 0).toLocaleString()}</h3>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            className="bg-gradient-to-br from-yellow-900/40 to-yellow-800/20 rounded-2xl p-6 border border-yellow-500/30"
          >
            <TrendingUp className="w-8 h-8 text-yellow-400 mb-2" />
            <p className="text-sm text-slate-400 mb-1">Conversion Rate</p>
            <h3 className="text-3xl font-black">{referralData?.stats?.conversion_rate ?? 0}%</h3>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="bg-gradient-to-br from-blue-900/40 to-blue-800/20 rounded-2xl p-6 border border-blue-500/30"
          >
            <LinkIcon className="w-8 h-8 text-blue-400 mb-2" />
            <p className="text-sm text-slate-400 mb-1">Link Clicks</p>
            <h3 className="text-3xl font-black">{referralData?.stats?.total_clicks || 0}</h3>
          </motion.div>
        </div>

        {/* Referral Link */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="bg-gradient-to-br from-slate-900/50 to-slate-800/30 rounded-2xl p-6 mb-8 border border-white/10"
        >
          <h2 className="text-xl font-bold mb-4">Your Referral Link</h2>
          <div className="flex gap-3">
            <div className="flex-1 bg-slate-800/50 border border-white/10 rounded-xl px-4 py-3 font-mono text-sm overflow-x-auto">
              {referralData ? referralLink : 'Loading…'}
            </div>
            <button
              onClick={copyReferralLink}
              className="bg-purple-600 hover:bg-purple-500 rounded-xl px-6 py-3 font-bold transition-all shadow-lg flex items-center gap-2"
            >
              {copied ? <Check className="w-5 h-5" /> : <Copy className="w-5 h-5" />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>

          {/* Social Share Buttons */}
          <div className="mt-4 flex gap-3">
            <button
              onClick={() => shareOnPlatform('twitter')}
              className="flex-1 bg-[#1DA1F2] hover:bg-[#1a8cd8] rounded-xl px-4 py-3 font-bold transition-all"
            >
              Share on Twitter
            </button>
            <button
              onClick={() => shareOnPlatform('discord')}
              className="flex-1 bg-[#5865F2] hover:bg-[#4752c4] rounded-xl px-4 py-3 font-bold transition-all"
            >
              Share on Discord
            </button>
            <button
              onClick={() => shareOnPlatform('reddit')}
              className="flex-1 bg-[#FF4500] hover:bg-[#e03d00] rounded-xl px-4 py-3 font-bold transition-all"
            >
              Share on Reddit
            </button>
          </div>
        </motion.div>

        {/* Rewards Grid */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          className="mb-8"
        >
          <h2 className="text-2xl font-bold mb-4">Rewards & Benefits</h2>
          <div className="grid md:grid-cols-2 gap-4">
            {rewards.map((reward, idx) => (
              <motion.div
                key={idx}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.7 + idx * 0.1 }}
                className={`rounded-2xl p-6 border ${
                  reward.highlight
                    ? 'bg-gradient-to-br from-purple-900/40 to-purple-800/20 border-purple-500/30 ring-2 ring-purple-500/20'
                    : 'bg-slate-900/50 border-white/10'
                }`}
              >
                <div className="text-4xl mb-3">{reward.icon}</div>
                <h3 className="text-xl font-bold mb-2">{reward.title}</h3>
                <p className="text-slate-300 mb-3">{reward.description}</p>
                <div className="flex items-center gap-2">
                  <div className="h-px flex-1 bg-gradient-to-r from-transparent via-white/20 to-transparent" />
                  <span className="text-xs text-slate-400 font-medium">{reward.requirement}</span>
                  <div className="h-px flex-1 bg-gradient-to-r from-transparent via-white/20 to-transparent" />
                </div>
              </motion.div>
            ))}
          </div>
        </motion.div>

        {/* Recent Referrals - the backend only returns an already-anonymized
            user_id prefix + joined_at (see app/routers/referrals.py), no
            username or premium status, so this only shows what's real. */}
        {referralData?.recent_referrals && referralData.recent_referrals.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.8 }}
            className="bg-slate-900/50 rounded-2xl border border-white/10 overflow-hidden"
          >
            <div className="p-6 border-b border-white/10">
              <h2 className="text-xl font-bold">Recent Referrals</h2>
            </div>
            <div className="divide-y divide-white/5">
              {referralData.recent_referrals.map((referral, idx) => (
                <div key={idx} className="p-4 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-600 to-purple-400 flex items-center justify-center font-bold">
                      {referral.user_id?.charAt(0).toUpperCase() || '?'}
                    </div>
                    <div>
                      <p className="font-bold font-mono text-sm">{referral.user_id || 'Anonymous'}</p>
                      <p className="text-sm text-slate-400">
                        {referral.joined_at ? new Date(referral.joined_at).toLocaleDateString() : '—'}
                      </p>
                    </div>
                  </div>
                  <span className="px-3 py-1 bg-emerald-500/20 text-emerald-400 text-sm font-bold rounded-full">
                    +{perConversion} coins
                  </span>
                </div>
              ))}
            </div>
          </motion.div>
        )}

        {/* How It Works */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.9 }}
          className="mt-8 bg-blue-900/20 border border-blue-500/30 rounded-xl p-6"
        >
          <h3 className="text-xl font-bold mb-4">How It Works</h3>
          <ol className="space-y-3">
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-purple-600 flex items-center justify-center text-sm font-bold flex-shrink-0">1</span>
              <p className="text-slate-300">Share your unique referral link with friends and trading communities</p>
            </li>
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-purple-600 flex items-center justify-center text-sm font-bold flex-shrink-0">2</span>
              <p className="text-slate-300">When they sign up using your link, you automatically earn {perConversion} coins</p>
            </li>
            <li className="flex gap-3">
              <span className="w-6 h-6 rounded-full bg-purple-600 flex items-center justify-center text-sm font-bold flex-shrink-0">3</span>
              <p className="text-slate-300">There's no cap - every friend who joins earns you more, with no limit on referrals</p>
            </li>
          </ol>
        </motion.div>
      </div>
    </div>
  );
};

export default ReferralProgram;
