import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Calculator, TrendingUp, AlertTriangle, Percent, DollarSign } from 'lucide-react';

const ProfitCalculator = () => {
  const [buyPrice, setBuyPrice] = useState('');
  const [sellPrice, setSellPrice] = useState('');
  const [quantity, setQuantity] = useState(1);
  const [eaTaxRate, setEaTaxRate] = useState(5); // EA takes 5%
  const [results, setResults] = useState(null);

  useEffect(() => {
    if (buyPrice && sellPrice) {
      calculateProfit();
    }
  }, [buyPrice, sellPrice, quantity, eaTaxRate]);

  const calculateProfit = () => {
    const buy = parseFloat(buyPrice) || 0;
    const sell = parseFloat(sellPrice) || 0;
    const qty = parseInt(quantity) || 1;
    const taxRate = parseFloat(eaTaxRate) / 100;

    const revenue = sell * qty;
    const eaTax = revenue * taxRate;
    const netRevenue = revenue - eaTax;
    const totalCost = buy * qty;
    const profit = netRevenue - totalCost;
    const profitPerUnit = profit / qty;
    const roi = totalCost > 0 ? (profit / totalCost) * 100 : 0;

    // Break-even calculation
    const breakEvenSell = buy / (1 - taxRate);

    // Profit targets
    const targets = [10, 25, 50, 100].map(targetRoi => {
      const targetProfit = totalCost * (targetRoi / 100);
      const requiredRevenue = totalCost + targetProfit;
      const requiredSell = requiredRevenue / (qty * (1 - taxRate));
      return { roi: targetRoi, sellPrice: Math.ceil(requiredSell) };
    });

    setResults({
      revenue,
      eaTax,
      netRevenue,
      totalCost,
      profit,
      profitPerUnit,
      roi,
      breakEvenSell,
      targets
    });
  };

  const formatCurrency = (value) => {
    return new Intl.NumberFormat('en-GB').format(Math.round(value));
  };

  const getROIColor = (roi) => {
    if (roi >= 50) return 'text-green-400';
    if (roi >= 20) return 'text-emerald-400';
    if (roi >= 10) return 'text-yellow-400';
    if (roi >= 0) return 'text-orange-400';
    return 'text-red-400';
  };

  return (
    <div className="min-h-screen bg-[#0e1320] text-white p-6">
      <div className="max-w-6xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-8"
        >
          <div className="flex items-center gap-3 mb-2">
            <Calculator className="w-8 h-8 text-purple-400" />
            <h1 className="text-4xl font-black">Profit Calculator</h1>
          </div>
          <p className="text-slate-400">
            Calculate profits with EA tax, break-even points, and ROI targets
          </p>
        </motion.div>

        <div className="grid lg:grid-cols-2 gap-6">
          {/* Input Panel */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            className="bg-gradient-to-br from-slate-900/50 to-slate-800/30 rounded-2xl p-6 border border-white/10"
          >
            <h2 className="text-xl font-bold mb-6">Trade Details</h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Buy Price (per card)
                </label>
                <input
                  type="number"
                  value={buyPrice}
                  onChange={(e) => setBuyPrice(e.target.value)}
                  placeholder="e.g., 10000"
                  className="w-full bg-slate-800/50 border border-white/10 rounded-xl px-4 py-3 text-white
                           focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 outline-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Sell Price (per card)
                </label>
                <input
                  type="number"
                  value={sellPrice}
                  onChange={(e) => setSellPrice(e.target.value)}
                  placeholder="e.g., 12000"
                  className="w-full bg-slate-800/50 border border-white/10 rounded-xl px-4 py-3 text-white
                           focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 outline-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  Quantity
                </label>
                <input
                  type="number"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  min="1"
                  className="w-full bg-slate-800/50 border border-white/10 rounded-xl px-4 py-3 text-white
                           focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 outline-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-400 mb-2">
                  EA Tax Rate (%)
                </label>
                <div className="flex items-center gap-4">
                  <input
                    type="range"
                    min="0"
                    max="10"
                    step="0.5"
                    value={eaTaxRate}
                    onChange={(e) => setEaTaxRate(e.target.value)}
                    className="flex-1"
                  />
                  <span className="text-lg font-bold w-16 text-right">{eaTaxRate}%</span>
                </div>
                <p className="text-xs text-slate-500 mt-1">Default: 5% (EA's standard tax)</p>
              </div>
            </div>
          </motion.div>

          {/* Results Panel */}
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            className="space-y-6"
          >
            {results && (
              <>
                {/* Main Result */}
                <div className={`rounded-2xl p-6 border-2 ${
                  results.profit >= 0
                    ? 'bg-gradient-to-br from-green-900/30 to-green-800/20 border-green-500/30'
                    : 'bg-gradient-to-br from-red-900/30 to-red-800/20 border-red-500/30'
                }`}>
                  <div className="text-center">
                    <p className="text-sm text-slate-400 mb-1">Net Profit</p>
                    <h2 className={`text-5xl font-black mb-2 ${results.profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {results.profit >= 0 ? '+' : ''}{formatCurrency(results.profit)}
                    </h2>
                    <div className="flex items-center justify-center gap-2">
                      <span className={`text-2xl font-bold ${getROIColor(results.roi)}`}>
                        {results.roi >= 0 ? '+' : ''}{results.roi.toFixed(2)}% ROI
                      </span>
                    </div>
                  </div>

                  <div className="mt-6 pt-6 border-t border-white/10 grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs text-slate-400 mb-1">Total Revenue</p>
                      <p className="font-bold">{formatCurrency(results.revenue)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400 mb-1">EA Tax</p>
                      <p className="font-bold text-red-400">-{formatCurrency(results.eaTax)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400 mb-1">Net Revenue</p>
                      <p className="font-bold text-green-400">{formatCurrency(results.netRevenue)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-slate-400 mb-1">Total Cost</p>
                      <p className="font-bold">-{formatCurrency(results.totalCost)}</p>
                    </div>
                  </div>
                </div>

                {/* Break-even */}
                <div className="bg-slate-900/50 rounded-2xl p-6 border border-white/10">
                  <div className="flex items-center gap-2 mb-4">
                    <AlertTriangle className="w-5 h-5 text-yellow-400" />
                    <h3 className="font-bold">Break-Even Point</h3>
                  </div>
                  <p className="text-slate-400 text-sm mb-2">
                    Minimum sell price to avoid loss (after EA tax):
                  </p>
                  <div className="bg-slate-800/50 rounded-xl p-4 border border-yellow-500/20">
                    <p className="text-3xl font-black text-yellow-400">
                      {formatCurrency(results.breakEvenSell)}
                    </p>
                  </div>
                </div>

                {/* ROI Targets */}
                <div className="bg-slate-900/50 rounded-2xl p-6 border border-white/10">
                  <div className="flex items-center gap-2 mb-4">
                    <TrendingUp className="w-5 h-5 text-purple-400" />
                    <h3 className="font-bold">Profit Targets</h3>
                  </div>
                  <div className="space-y-2">
                    {results.targets.map((target, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between p-3 bg-slate-800/30 rounded-lg border border-white/5"
                      >
                        <span className="text-sm">
                          <Percent className="w-4 h-4 inline mr-1" />
                          {target.roi}% ROI
                        </span>
                        <span className="font-bold text-purple-400">
                          Sell @ {formatCurrency(target.sellPrice)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}

            {!results && (
              <div className="bg-slate-900/50 rounded-2xl p-12 border border-white/10 text-center">
                <DollarSign className="w-16 h-16 text-slate-600 mx-auto mb-4" />
                <p className="text-slate-400">Enter buy and sell prices to calculate profit</p>
              </div>
            )}
          </motion.div>
        </div>

        {/* Quick Tips */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="mt-6 bg-blue-900/20 border border-blue-500/30 rounded-xl p-4"
        >
          <h4 className="font-bold text-blue-400 mb-2">💡 Pro Tips</h4>
          <ul className="text-sm text-slate-300 space-y-1">
            <li>• EA tax is always 5% of the sale price (non-negotiable)</li>
            <li>• Aim for at least 10-15% ROI to account for market volatility</li>
            <li>• Factor in the time value - quick flips are better than slow gains</li>
            <li>• Use profit targets to set lazy listing prices for automated sales</li>
          </ul>
        </motion.div>
      </div>
    </div>
  );
};

export default ProfitCalculator;
