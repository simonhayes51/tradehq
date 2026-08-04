import React, { useEffect, useState } from "react";
import { useDashboard } from "../context/DashboardContext";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";

const ProfitGraph = () => {
  const { getAllTrades } = useDashboard();
  const [chartData, setChartData] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const processTradesForChart = async () => {
      try {
        const result = await getAllTrades();
        if (result.success) {
          // Process trades to create cumulative profit over time
          const sortedTrades = result.trades
            .filter(trade => trade.timestamp && trade.profit !== null)
            .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

          let cumulativeProfit = 0;
          const data = sortedTrades.map(trade => {
            cumulativeProfit += trade.profit;
            return {
              date: new Date(trade.timestamp).toLocaleDateString(),
              profit: cumulativeProfit,
              tradeName: `${trade.player} (${trade.version})`
            };
          });

          setChartData(data);
        }
      } catch (err) {
        console.error("Failed to process chart data:", err);
      } finally {
        setLoading(false);
      }
    };

    processTradesForChart();
  }, [getAllTrades]);

  if (loading) {
    return <div className="text-gray-400">Loading chart data...</div>;
  }

  return (
    <div className="bg-zinc-900 p-6 rounded-xl shadow-lg text-white">
      <h2 className="text-2xl font-bold mb-4">Profit Over Time</h2>
      {chartData.length === 0 ? (
        <p className="text-gray-400">No trade data available for chart.</p>
      ) : (
        <ResponsiveContainer width="100%" height={400}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" stroke="#9CA3AF" />
            <YAxis stroke="#9CA3AF" />
            <Tooltip 
              contentStyle={{ 
                backgroundColor: '#1F2937', 
                border: '1px solid #374151',
                borderRadius: '8px'
              }}
            />
            <Line 
              type="monotone" 
              dataKey="profit" 
              stroke="#10B981" 
              strokeWidth={3}
              dot={{ fill: '#10B981', strokeWidth: 2, r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};

export default ProfitGraph;
