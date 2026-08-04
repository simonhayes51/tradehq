import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { TrendingUp, AlertCircle, Target, Sparkles, BarChart3, Shield, Zap } from 'lucide-react';
import api from '../axios';
import toast from 'react-hot-toast';

const PortfolioOptimizer = () => {
  const [loading, setLoading] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [portfolio, setPortfolio] = useState([]);
  const [optimizationGoal, setOptimizationGoal] = useState('balanced');

  useEffect(() => {
    fetchPortfolio();
  }, []);

  const fetchPortfolio = async () => {
    try {
      const { data } = await api.get('/api/trades');
      setPortfolio(data.trades || []);
    } catch (error) {
      toast.error(error.userMessage || 'Failed to fetch portfolio');
    }
  };

  const runOptimization = async () => {
    setLoading(true);
    try {
      const { data } = await api.post('/api/ai/optimize-portfolio', {
        goal: optimizationGoal,
        trades: portfolio
      });
      setAnalysis(data);
      toast.success('Portfolio optimized successfully!');
    } catch (error) {
      toast.error(error.userMessage || 'Optimization failed');
    } finally {
      setLoading(false);
    }
  };

  const riskLevelColor = (level) => {
    switch(level) {
      case 'low': return 'text-green-400 bg-green-400/10';
      case 'medium': return 'text-yellow-400 bg-yellow-400/10';
      case 'high': return 'text-red-400 bg-red-400/10';
      default: return 'text-slate-400 bg-slate-400/10';
    }
  };

  return (
    <div className="min-h-screen bg-[#0e1320] text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-8"
        >
          <div className="flex items-center gap-3 mb-2">
            <Sparkles className="w-8 h-8 text-purple-400" />
            <h1 className="text-4xl font-black">AI Portfolio Optimizer</h1>
          </div>
          <p className="text-slate-400">
            Advanced AI analysis to maximize returns and minimize risk
          </p>
        </motion.div>

        {/* Optimization Controls */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
          className="bg-gradient-to-br from-slate-900/50 to-slate-800/30 rounded-2xl p-6 mb-6 border border-white/10"
        >
          <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
            <Target className="w-5 h-5 text-purple-400" />
            Optimization Strategy
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <button
              onClick={() => setOptimizationGoal('aggressive')}
              className={`p-4 rounded-xl border-2 transition-all ${
                optimizationGoal === 'aggressive'
                  ? 'border-red-500 bg-red-500/10'
                  : 'border-white/10 bg-slate-800/30 hover:border-red-500/50'
              }`}
            >
              <Zap className="w-6 h-6 text-red-400 mb-2" />
              <h3 className="font-bold mb-1">Aggressive</h3>
              <p className="text-sm text-slate-400">High risk, high reward</p>
            </button>

            <button
              onClick={() => setOptimizationGoal('balanced')}
              className={`p-4 rounded-xl border-2 transition-all ${
                optimizationGoal === 'balanced'
                  ? 'border-purple-500 bg-purple-500/10'
                  : 'border-white/10 bg-slate-800/30 hover:border-purple-500/50'
              }`}
            >
              <BarChart3 className="w-6 h-6 text-purple-400 mb-2" />
              <h3 className="font-bold mb-1">Balanced</h3>
              <p className="text-sm text-slate-400">Optimal risk/reward ratio</p>
            </button>

            <button
              onClick={() => setOptimizationGoal('conservative')}
              className={`p-4 rounded-xl border-2 transition-all ${
                optimizationGoal === 'conservative'
                  ? 'border-green-500 bg-green-500/10'
                  : 'border-white/10 bg-slate-800/30 hover:border-green-500/50'
              }`}
            >
              <Shield className="w-6 h-6 text-green-400 mb-2" />
              <h3 className="font-bold mb-1">Conservative</h3>
              <p className="text-sm text-slate-400">Low risk, steady gains</p>
            </button>
          </div>

          <button
            onClick={runOptimization}
            disabled={loading || portfolio.length === 0}
            className="w-full bg-gradient-to-r from-purple-600 to-purple-500 hover:from-purple-500 hover:to-purple-400
                     disabled:from-slate-700 disabled:to-slate-600 disabled:cursor-not-allowed
                     rounded-xl py-3 px-6 font-bold text-white transition-all shadow-lg"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                Analyzing Portfolio...
              </span>
            ) : (
              `Optimize ${portfolio.length} Trades`
            )}
          </button>
        </motion.div>

        {/* Analysis Results */}
        {analysis && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="space-y-6"
          >
            {/* Portfolio Score */}
            <div className="bg-gradient-to-br from-purple-900/30 to-purple-800/20 rounded-2xl p-6 border border-purple-500/30">
              <h2 className="text-2xl font-bold mb-4">Portfolio Health Score</h2>
              <div className="flex items-center gap-6">
                <div className="relative w-32 h-32">
                  <svg className="w-32 h-32 transform -rotate-90">
                    <circle
                      cx="64"
                      cy="64"
                      r="56"
                      stroke="currentColor"
                      strokeWidth="8"
                      fill="none"
                      className="text-slate-700"
                    />
                    <circle
                      cx="64"
                      cy="64"
                      r="56"
                      stroke="currentColor"
                      strokeWidth="8"
                      fill="none"
                      strokeDasharray={`${2 * Math.PI * 56}`}
                      strokeDashoffset={`${2 * Math.PI * 56 * (1 - (analysis.score || 0) / 100)}`}
                      className="text-purple-400 transition-all duration-1000"
                    />
                  </svg>
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-3xl font-black">{analysis.score || 0}</span>
                  </div>
                </div>
                <div className="flex-1">
                  <p className="text-slate-300 mb-2">{analysis.scoreDescription}</p>
                  <div className="flex gap-2 flex-wrap">
                    <span className={`px-3 py-1 rounded-full text-sm font-bold ${riskLevelColor(analysis.riskLevel)}`}>
                      {analysis.riskLevel?.toUpperCase()} RISK
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Recommendations */}
            <div className="bg-slate-900/50 rounded-2xl p-6 border border-white/10">
              <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
                <TrendingUp className="w-6 h-6 text-green-400" />
                AI Recommendations
              </h2>
              <div className="space-y-3">
                {analysis.recommendations?.map((rec, idx) => (
                  <motion.div
                    key={idx}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: idx * 0.1 }}
                    className="flex gap-4 p-4 bg-slate-800/30 rounded-xl border border-white/5"
                  >
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                      rec.priority === 'high' ? 'bg-red-500/20 text-red-400' :
                      rec.priority === 'medium' ? 'bg-yellow-500/20 text-yellow-400' :
                      'bg-blue-500/20 text-blue-400'
                    }`}>
                      {rec.priority === 'high' ? '!' : rec.priority === 'medium' ? '•' : 'i'}
                    </div>
                    <div className="flex-1">
                      <h3 className="font-bold mb-1">{rec.title}</h3>
                      <p className="text-sm text-slate-400">{rec.description}</p>
                      {rec.expectedGain && (
                        <p className="text-sm text-green-400 mt-2">
                          Expected gain: +{rec.expectedGain}%
                        </p>
                      )}
                    </div>
                  </motion.div>
                ))}
              </div>
            </div>

            {/* Diversification Analysis */}
            {analysis.diversification && (
              <div className="grid md:grid-cols-2 gap-6">
                <div className="bg-slate-900/50 rounded-2xl p-6 border border-white/10">
                  <h3 className="font-bold mb-4">Position Concentration</h3>
                  <div className="space-y-3">
                    {analysis.diversification.topPositions?.map((pos, idx) => (
                      <div key={idx}>
                        <div className="flex justify-between text-sm mb-1">
                          <span>{pos.player}</span>
                          <span className="text-purple-400">{pos.percentage}%</span>
                        </div>
                        <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-purple-600 to-purple-400"
                            style={{ width: `${pos.percentage}%` }}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="bg-slate-900/50 rounded-2xl p-6 border border-white/10">
                  <h3 className="font-bold mb-4">Risk Metrics</h3>
                  <div className="space-y-4">
                    <div>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-400">Sharpe Ratio</span>
                        <span className="font-bold">{analysis.metrics?.sharpe || 'N/A'}</span>
                      </div>
                    </div>
                    <div>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-400">Max Drawdown</span>
                        <span className="font-bold text-red-400">
                          {analysis.metrics?.maxDrawdown || 'N/A'}%
                        </span>
                      </div>
                    </div>
                    <div>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-slate-400">Win Rate</span>
                        <span className="font-bold text-green-400">
                          {analysis.metrics?.winRate || 'N/A'}%
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </motion.div>
        )}

        {/* Empty State */}
        {!analysis && !loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-center py-20"
          >
            <AlertCircle className="w-16 h-16 text-slate-600 mx-auto mb-4" />
            <h3 className="text-xl font-bold text-slate-400 mb-2">
              {portfolio.length === 0 ? 'No trades found' : 'Ready to optimize'}
            </h3>
            <p className="text-slate-500">
              {portfolio.length === 0
                ? 'Add some trades to get started with portfolio optimization'
                : 'Select your strategy and run the optimizer to get AI-powered insights'}
            </p>
          </motion.div>
        )}
      </div>
    </div>
  );
};

export default PortfolioOptimizer;
