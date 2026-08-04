import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Zap, Plus, Trash2, Upload, Download, Calculator, TrendingUp } from 'lucide-react';
import api from '../axios';
import toast from 'react-hot-toast';

const MarketMaker = () => {
  const [bulkTrades, setBulkTrades] = useState([]);
  const [csvInput, setCsvInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState(null);

  const addTrade = () => {
    setBulkTrades([...bulkTrades, {
      id: Date.now(),
      player: '',
      buyPrice: '',
      sellPrice: '',
      quantity: 1,
      platform: 'ps'
    }]);
  };

  const removeTrade = (id) => {
    setBulkTrades(bulkTrades.filter(t => t.id !== id));
  };

  const updateTrade = (id, field, value) => {
    setBulkTrades(bulkTrades.map(t =>
      t.id === id ? { ...t, [field]: value } : t
    ));
  };

  const calculateSummary = () => {
    const eaTaxRate = 0.05;
    let totalInvestment = 0;
    let totalRevenue = 0;
    let totalProfit = 0;

    bulkTrades.forEach(trade => {
      const buy = parseFloat(trade.buyPrice) || 0;
      const sell = parseFloat(trade.sellPrice) || 0;
      const qty = parseInt(trade.quantity) || 1;

      const investment = buy * qty;
      const revenue = sell * qty;
      const eaTax = revenue * eaTaxRate;
      const netRevenue = revenue - eaTax;
      const profit = netRevenue - investment;

      totalInvestment += investment;
      totalRevenue += revenue;
      totalProfit += profit;
    });

    setSummary({
      totalTrades: bulkTrades.length,
      totalInvestment,
      totalRevenue,
      totalProfit,
      roi: totalInvestment > 0 ? (totalProfit / totalInvestment) * 100 : 0
    });
  };

  const importCSV = () => {
    try {
      const lines = csvInput.trim().split('\n');
      const imported = lines.slice(1).map((line, idx) => { // Skip header
        const [player, buyPrice, sellPrice, quantity, platform] = line.split(',');
        return {
          id: Date.now() + idx,
          player: player?.trim() || '',
          buyPrice: buyPrice?.trim() || '',
          sellPrice: sellPrice?.trim() || '',
          quantity: parseInt(quantity?.trim()) || 1,
          platform: platform?.trim() || 'ps'
        };
      });

      setBulkTrades([...bulkTrades, ...imported]);
      setCsvInput('');
      toast.success(`Imported ${imported.length} trades`);
    } catch (error) {
      toast.error('Failed to parse CSV. Check format.');
    }
  };

  const exportCSV = () => {
    const header = 'Player,Buy Price,Sell Price,Quantity,Platform\n';
    const rows = bulkTrades.map(t =>
      `${t.player},${t.buyPrice},${t.sellPrice},${t.quantity},${t.platform}`
    ).join('\n');
    const csv = header + rows;

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `market-maker-${Date.now()}.csv`;
    a.click();

    toast.success('CSV exported!');
  };

  const executeBulkTrades = async () => {
    if (bulkTrades.length === 0) {
      toast.error('Add some trades first');
      return;
    }

    // A freshly-added row (or a malformed CSV line) starts with blank
    // buy/sell prices - parseInt('') is NaN, which JSON.stringify turns into
    // null, which the backend's TradeItem model rejects outright. Catch that
    // here with a specific message instead of sending it and getting back an
    // opaque error.
    const rowErrors = [];
    bulkTrades.forEach((t, idx) => {
      const buy = parseInt(t.buyPrice, 10);
      const sell = parseInt(t.sellPrice, 10);
      const qty = parseInt(t.quantity, 10);
      const label = t.player?.trim() || `Row ${idx + 1}`;
      if (!t.player?.trim()) rowErrors.push(`${label}: missing player name`);
      if (!Number.isFinite(buy) || buy <= 0) rowErrors.push(`${label}: buy price is missing or invalid`);
      if (!Number.isFinite(sell) || sell <= 0) rowErrors.push(`${label}: sell price is missing or invalid`);
      if (Number.isFinite(buy) && Number.isFinite(sell) && buy >= sell) rowErrors.push(`${label}: buy price must be less than sell price`);
      if (!Number.isFinite(qty) || qty <= 0) rowErrors.push(`${label}: quantity is missing or invalid`);
    });

    if (rowErrors.length) {
      toast.error(
        rowErrors.length === 1
          ? rowErrors[0]
          : `${rowErrors.length} rows need fixing before you can execute:\n${rowErrors.slice(0, 3).join('\n')}${rowErrors.length > 3 ? '\n…' : ''}`
      );
      return;
    }

    setLoading(true);
    try {
      const { data } = await api.post('/api/trades/bulk', {
        // Backend's TradeItem model (backend/app/routers/trades.py) requires
        // `buy`/`sell` (not buyPrice/sellPrice) and expects integer prices.
        trades: bulkTrades.map(t => ({
          player: t.player,
          buy: parseInt(t.buyPrice, 10),
          sell: parseInt(t.sellPrice, 10),
          quantity: parseInt(t.quantity, 10),
          platform: t.platform
        }))
      });

      if (data.ok === false) {
        // Backend validated per-row and rejected some (e.g. buy >= sell
        // slipped past the client check some other way) - it returns 200
        // with ok:false and a list of per-row errors rather than a 4xx/5xx.
        toast.error(data.message || 'Some trades failed validation');
        return;
      }

      toast.success(`Successfully logged ${data.inserted_count} trades!`);
      setBulkTrades([]);
      setSummary(null);
    } catch (error) {
      toast.error(error.userMessage || 'Failed to execute bulk trades');
    } finally {
      setLoading(false);
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
            <Zap className="w-8 h-8 text-yellow-400" />
            <h1 className="text-4xl font-black">Market Maker Mode</h1>
          </div>
          <p className="text-slate-400">
            Bulk trade entry for power traders • Import from CSV • Calculate ROI
          </p>
        </motion.div>

        <div className="grid lg:grid-cols-3 gap-6">
          {/* Main Panel */}
          <div className="lg:col-span-2 space-y-6">
            {/* Actions */}
            <div className="flex gap-3">
              <button
                onClick={addTrade}
                className="flex items-center gap-2 bg-purple-600 hover:bg-purple-500 rounded-xl px-4 py-2 font-bold transition-all"
              >
                <Plus className="w-5 h-5" />
                Add Trade
              </button>
              <button
                onClick={calculateSummary}
                className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 rounded-xl px-4 py-2 font-bold transition-all"
              >
                <Calculator className="w-5 h-5" />
                Calculate
              </button>
              <button
                onClick={exportCSV}
                disabled={bulkTrades.length === 0}
                className="flex items-center gap-2 bg-slate-700 hover:bg-slate-600 disabled:opacity-50 rounded-xl px-4 py-2 font-bold transition-all"
              >
                <Download className="w-5 h-5" />
                Export CSV
              </button>
            </div>

            {/* Trades Table */}
            <div className="bg-slate-900/50 rounded-2xl border border-white/10 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-slate-800/50 border-b border-white/10">
                    <tr>
                      <th className="text-left p-3 font-bold text-sm">Player</th>
                      <th className="text-left p-3 font-bold text-sm">Buy</th>
                      <th className="text-left p-3 font-bold text-sm">Sell</th>
                      <th className="text-left p-3 font-bold text-sm">Qty</th>
                      <th className="text-left p-3 font-bold text-sm">Platform</th>
                      <th className="w-10"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {bulkTrades.map((trade) => (
                      <tr key={trade.id}>
                        <td className="p-2">
                          <input
                            type="text"
                            value={trade.player}
                            onChange={(e) => updateTrade(trade.id, 'player', e.target.value)}
                            placeholder="Player name"
                            className="w-full bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2 text-sm"
                          />
                        </td>
                        <td className="p-2">
                          <input
                            type="number"
                            value={trade.buyPrice}
                            onChange={(e) => updateTrade(trade.id, 'buyPrice', e.target.value)}
                            placeholder="0"
                            className="w-full bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2 text-sm"
                          />
                        </td>
                        <td className="p-2">
                          <input
                            type="number"
                            value={trade.sellPrice}
                            onChange={(e) => updateTrade(trade.id, 'sellPrice', e.target.value)}
                            placeholder="0"
                            className="w-full bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2 text-sm"
                          />
                        </td>
                        <td className="p-2">
                          <input
                            type="number"
                            value={trade.quantity}
                            onChange={(e) => updateTrade(trade.id, 'quantity', e.target.value)}
                            min="1"
                            className="w-20 bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2 text-sm"
                          />
                        </td>
                        <td className="p-2">
                          <select
                            value={trade.platform}
                            onChange={(e) => updateTrade(trade.id, 'platform', e.target.value)}
                            className="bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2 text-sm"
                          >
                            <option value="ps">PS</option>
                            <option value="xbox">Xbox</option>
                            <option value="pc">PC</option>
                          </select>
                        </td>
                        <td className="p-2">
                          <button
                            onClick={() => removeTrade(trade.id)}
                            className="text-red-400 hover:text-red-300 p-1"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {bulkTrades.length === 0 && (
                <div className="text-center py-12 text-slate-400">
                  <Zap className="w-12 h-12 mx-auto mb-3 text-slate-600" />
                  <p>No trades yet. Add trades or import from CSV.</p>
                </div>
              )}
            </div>

            {/* Execute Button */}
            {bulkTrades.length > 0 && (
              <button
                onClick={executeBulkTrades}
                disabled={loading}
                className="w-full bg-gradient-to-r from-green-600 to-green-500 hover:from-green-500 hover:to-green-400
                         disabled:from-slate-700 disabled:to-slate-600 disabled:cursor-not-allowed
                         rounded-xl py-4 px-6 font-bold text-white transition-all shadow-lg"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Executing {bulkTrades.length} Trades...
                  </span>
                ) : (
                  `Execute ${bulkTrades.length} Trade${bulkTrades.length > 1 ? 's' : ''}`
                )}
              </button>
            )}
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            {/* Summary */}
            {summary && (
              <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="bg-gradient-to-br from-purple-900/30 to-purple-800/20 rounded-2xl p-6 border border-purple-500/30"
              >
                <h3 className="text-xl font-bold mb-4 flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-purple-400" />
                  Summary
                </h3>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-slate-400">Total Trades:</span>
                    <span className="font-bold">{summary.totalTrades}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Investment:</span>
                    <span className="font-bold">{summary.totalInvestment.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">Revenue:</span>
                    <span className="font-bold text-green-400">{summary.totalRevenue.toLocaleString()}</span>
                  </div>
                  <div className="h-px bg-white/10" />
                  <div className="flex justify-between">
                    <span className="text-slate-400">Net Profit:</span>
                    <span className={`font-bold text-lg ${summary.totalProfit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {summary.totalProfit >= 0 ? '+' : ''}{summary.totalProfit.toLocaleString()}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-400">ROI:</span>
                    <span className={`font-bold ${summary.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {summary.roi >= 0 ? '+' : ''}{summary.roi.toFixed(2)}%
                    </span>
                  </div>
                </div>
              </motion.div>
            )}

            {/* CSV Import */}
            <div className="bg-slate-900/50 rounded-2xl p-6 border border-white/10">
              <h3 className="font-bold mb-3 flex items-center gap-2">
                <Upload className="w-5 h-5 text-blue-400" />
                Import CSV
              </h3>
              <textarea
                value={csvInput}
                onChange={(e) => setCsvInput(e.target.value)}
                placeholder="Player,Buy Price,Sell Price,Quantity,Platform&#10;Mbappe,50000,55000,2,ps&#10;Haaland,45000,48000,1,xbox"
                className="w-full bg-slate-800/50 border border-white/10 rounded-lg px-3 py-2 text-sm font-mono h-32 resize-none"
              />
              <button
                onClick={importCSV}
                disabled={!csvInput.trim()}
                className="w-full mt-3 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 rounded-lg py-2 font-bold text-sm transition-all"
              >
                Import
              </button>
            </div>

            {/* Instructions */}
            <div className="bg-blue-900/20 border border-blue-500/30 rounded-xl p-4">
              <h4 className="font-bold text-blue-400 mb-2">💡 Quick Tips</h4>
              <ul className="text-sm text-slate-300 space-y-1">
                <li>• Use CSV for bulk imports</li>
                <li>• Calculate before executing</li>
                <li>• 5% EA tax is auto-calculated</li>
                <li>• Export for record keeping</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MarketMaker;
