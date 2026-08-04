// Enhanced src/pages/SmartBuy.jsx with performance optimizations and better UX
import React, { useState, useEffect, useMemo, useCallback, createContext, useContext } from "react";
import { 
  Brain, 
  TrendingUp, 
  TrendingDown, 
  AlertTriangle, 
  Target,
  Clock,
  Settings,
  RefreshCw,
  Star,
  Eye,
  ShoppingCart,
  BarChart3,
  Zap,
  Filter,
  ChevronDown,
  Info,
  X
} from "lucide-react";

// ========================================
// CONSTANTS
// ========================================

export const MARKET_STATES = {
  NORMAL: 'normal',
  PRE_CRASH: 'pre_crash', 
  CRASH_ACTIVE: 'crash_active',
  RECOVERY: 'recovery',
  PROMO_HYPE: 'promo_hype'
};

export const BUY_CATEGORIES = {
  CRASH_OPPORTUNITY: 'crash_opportunity',
  PROMO_INVESTMENT: 'promo_investment', 
  UNDERVALUED: 'undervalued',
  RISING_STAR: 'rising_star',
  QUICK_FLIP: 'quick_flip',
  LONG_TERM_HOLD: 'long_term_hold',
  EVENT_DRIVEN: 'event_driven',
  MARKET_CORRECTION: 'market_correction'
};

// ========================================
// API FUNCTIONS - YOUR BACKEND ENDPOINTS
// ========================================

const API_BASE_URL = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || 'https://api.futhub.co.uk';

export async function fetchSmartBuyData(filters = {}) {
  try {
    // Convert filters to query parameters for your GET endpoint
    const params = new URLSearchParams({
      budget: filters.budget || 100000,
      risk_tolerance: filters.risk_tolerance || 'moderate',
      time_horizon: filters.time_horizon || 'short_term',
      platform: filters.platform || 'ps',
      limit: 30
    });

    // Add optional rating filters only if they exist
    if (filters.min_rating) params.append('min_rating', filters.min_rating);
    if (filters.max_rating) params.append('max_rating', filters.max_rating);

    const response = await fetch(`${API_BASE_URL}/api/smart-buy/suggestions?${params}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include' // Important for session-based auth
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    
    // Transform to match expected format
    return {
      suggestions: data.suggestions || data.items || [],
      market_state: 'normal', // Your backend doesn't return this in suggestions endpoint
      timestamp: new Date().toISOString(),
      total_opportunities: data.count || (data.suggestions?.length || 0)
    };
  } catch (error) {
    console.error('Failed to fetch smart buy data:', error);
    throw new Error(`Failed to fetch smart buy data: ${error.message}`);
  }
}

export async function fetchMarketIntelligence() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/smart-buy/market-intelligence`, {
      credentials: 'include'
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();

    // Only surface fields the backend actually returns
    // (GET /api/smart-buy/market-intelligence -> { state, confidence, platform, detected_at })
    return {
      market_state: data.state || 'normal',
      current_state_confidence: typeof data.confidence === 'number' ? data.confidence : null,
      platform: data.platform || null,
      detected_at: data.detected_at || null
    };
  } catch (error) {
    console.error('Failed to fetch market intelligence:', error);
    throw new Error(`Failed to fetch market intelligence: ${error.message}`);
  }
}

export async function fetchSuggestionStats() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/smart-buy/stats`, {
      credentials: 'include'
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();

    // Only surface fields the backend actually returns
    // (GET /api/smart-buy/stats -> { suggestions_taken, success_rate }).
    // success_rate isn't computed by the backend yet (always 0), so we don't
    // treat it as a real measured stat - just pass through whatever came back
    // and let the UI decide whether there's enough to show.
    return {
      suggestions_taken: typeof data.suggestions_taken === 'number' ? data.suggestions_taken : null,
      success_rate: typeof data.success_rate === 'number' ? data.success_rate : null
    };
  } catch (error) {
    console.error('Failed to fetch suggestion stats:', error);
    throw new Error(`Failed to fetch suggestion stats: ${error.message}`);
  }
}

export async function submitSuggestionFeedback(cardId, action) {
  try {
    const response = await fetch(`${API_BASE_URL}/api/smart-buy/feedback`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include',
      body: JSON.stringify({
        card_id: cardId,
        action: action,
        timestamp: new Date().toISOString()
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Failed to submit feedback:', error);
    throw new Error(`Failed to submit feedback: ${error.message}`);
  }
}

export async function addWatch(watchData) {
  try {
    const response = await fetch(`${API_BASE_URL}/api/watchlist`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include', // required for session-based auth, matches other calls in this file
      body: JSON.stringify(watchData)
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Failed to add to watchlist:', error);
    throw new Error(`Failed to add to watchlist: ${error.message}`);
  }
}

// ========================================
// SETTINGS CONTEXT (minimal for currency formatting)
// ========================================

const SettingsContext = createContext();

export function useSettings() {
  const context = useContext(SettingsContext);
  if (!context) {
    // Fallback if context not available
    return {
      formatCurrency: (amount) => {
        if (typeof amount !== 'number') return '0 coins';
        if (amount >= 1000000) return `${(amount / 1000000).toFixed(1)}M coins`;
        if (amount >= 1000) return `${(amount / 1000).toFixed(1)}K coins`;
        return `${amount.toLocaleString()} coins`;
      }
    };
  }
  return context;
}

function SettingsProvider({ children }) {
  const formatCurrency = (amount, options = {}) => {
    const { showSymbol = true, abbreviated = true, precision = 0 } = options;
    
    if (typeof amount !== 'number') return showSymbol ? '0 coins' : '0';

    let formattedAmount;
    if (abbreviated && amount >= 1000000) {
      formattedAmount = (amount / 1000000).toFixed(1) + 'M';
    } else if (abbreviated && amount >= 1000) {
      formattedAmount = (amount / 1000).toFixed(1) + 'K';
    } else {
      formattedAmount = amount.toLocaleString('en-US', {
        minimumFractionDigits: precision,
        maximumFractionDigits: precision
      });
    }

    return showSymbol ? `${formattedAmount} coins` : formattedAmount;
  };

  return (
    <SettingsContext.Provider value={{ formatCurrency }}>
      {children}
    </SettingsContext.Provider>
  );
}

// ========================================
// COMPONENTS (YOUR ORIGINAL CODE)
// ========================================

const ACCENT = "#91db32";
const cardBase = "bg-gray-900/70 rounded-2xl p-4 border border-gray-800 hover:border-gray-700 transition-all duration-200";

// Performance: Memoized market state indicator
const MarketStateIndicator = React.memo(({ state, confidence, intelligence }) => {
  const stateConfig = {
    [MARKET_STATES.NORMAL]: { 
      color: "text-blue-400", 
      bg: "bg-blue-400/10", 
      label: "Normal Trading",
      icon: <BarChart3 size={16} />,
      description: "Standard market conditions"
    },
    [MARKET_STATES.PRE_CRASH]: { 
      color: "text-yellow-400", 
      bg: "bg-yellow-400/10", 
      label: "Pre-Crash Window",
      icon: <AlertTriangle size={16} />,
      description: "Market showing signs of instability"
    },
    [MARKET_STATES.CRASH_ACTIVE]: { 
      color: "text-red-400", 
      bg: "bg-red-400/10", 
      label: "Market Crash Active",
      icon: <TrendingDown size={16} />,
      description: "Prices dropping rapidly - buying opportunity"
    },
    [MARKET_STATES.RECOVERY]: { 
      color: "text-green-400", 
      bg: "bg-green-400/10", 
      label: "Recovery Phase",
      icon: <TrendingUp size={16} />,
      description: "Market recovering from crash"
    },
    [MARKET_STATES.PROMO_HYPE]: { 
      color: "text-purple-400", 
      bg: "bg-purple-400/10", 
      label: "Promo Hype",
      icon: <Zap size={16} />,
      description: "New promo causing market excitement"
    }
  };

  const config = stateConfig[state] || stateConfig[MARKET_STATES.NORMAL];

  return (
    <div className={`${cardBase} ${config.bg} border-opacity-50 cursor-help group relative`}>
      <div className="flex items-center gap-3">
        <div className={`${config.color}`}>
          {config.icon}
        </div>
        <div className="flex-1">
          <div className={`font-semibold ${config.color}`}>{config.label}</div>
          <div className="text-xs text-gray-400">
            Confidence: {typeof confidence === 'number' ? `${confidence}%` : 'Not enough data yet'}
          </div>
        </div>
        <Info size={14} className="text-gray-500" />
      </div>

      {/* Tooltip */}
      <div className="absolute bottom-full left-0 mb-2 hidden group-hover:block z-10">
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 text-sm w-64 shadow-xl">
          <div className={`font-medium ${config.color} mb-1`}>{config.label}</div>
          <div className="text-gray-300 mb-2">{config.description}</div>
          {intelligence?.detected_at && (
            <div className="text-xs text-gray-400">
              Last detected: {new Date(intelligence.detected_at).toLocaleString()}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});

// Performance: Memoized suggestion card with action optimizations
const SuggestionCard = React.memo(({ suggestion, onBuy, onWatchlist, onIgnore }) => {
  const { formatCurrency } = useSettings();
  const [loading, setLoading] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const handleAction = useCallback(async (action) => {
    setLoading(true);
    try {
      if (action === "buy") {
        await onBuy(suggestion);
      } else if (action === "watchlist") {
        await onWatchlist(suggestion);
      } else if (action === "ignore") {
        setDismissed(true);
        await onIgnore(suggestion);
      }
      
      // Submit feedback to improve suggestions
      await submitSuggestionFeedback(suggestion.card_id, action);
    } catch (error) {
      console.error(`Action ${action} failed:`, error);
    } finally {
      setLoading(false);
    }
  }, [suggestion, onBuy, onWatchlist, onIgnore]);

  // Memoized calculations
  const { profitColor, riskColor, profitPercentage } = useMemo(() => {
    const profitColor = suggestion.expected_profit >= 0 ? "text-green-400" : "text-red-400";
    const riskColor = suggestion.risk_level === "low" ? "text-green-400" : 
                     suggestion.risk_level === "medium" ? "text-yellow-400" : "text-red-400";
    const profitPercentage = ((suggestion.expected_profit / suggestion.current_price) * 100).toFixed(1);
    
    return { profitColor, riskColor, profitPercentage };
  }, [suggestion.expected_profit, suggestion.current_price, suggestion.risk_level]);

  if (dismissed) {
    return null;
  }

  return (
    <div className={`${cardBase} transform hover:scale-[1.01] transition-all duration-200`}>
      <div className="flex items-start gap-4">
        {/* Player Image with loading state */}
        <div className="relative">
          <img
            src={suggestion.image_url || "/img/card-placeholder.png"}
            alt={suggestion.name}
            className="w-20 h-28 object-contain rounded-lg bg-gray-800/50"
            loading="lazy"
            onError={(e) => {
              e.currentTarget.src = "/img/card-placeholder.png";
            }}
          />
          <div className="absolute -top-1 -right-1">
            <div className="w-6 h-6 rounded-full bg-gradient-to-r from-green-400 to-blue-500 flex items-center justify-center text-xs font-bold text-black">
              {suggestion.priority_score}
            </div>
          </div>
        </div>

        {/* Details */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between mb-2">
            <div>
              <h3 className="font-semibold text-white text-lg">{suggestion.name}</h3>
              <div className="flex items-center gap-2 text-sm text-gray-400">
                <span>{suggestion.rating}</span>
                <span>•</span>
                <span>{suggestion.position}</span>
                <span>•</span>
                <span>{suggestion.version || "Base"}</span>
              </div>
            </div>
            <div className={`px-2 py-1 rounded-full text-xs font-semibold ${riskColor} bg-black/20`}>
              {suggestion.risk_level?.toUpperCase()} RISK
            </div>
          </div>

          {/* Enhanced Price Info with profit percentage */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div className="bg-gray-800/50 rounded-lg p-3">
              <div className="text-xs text-gray-400">Current Price</div>
              <div className="font-semibold text-white">{formatCurrency(suggestion.current_price)}</div>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <div className="text-xs text-gray-400">Target Sell</div>
              <div className="font-semibold text-white">{formatCurrency(suggestion.target_price)}</div>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <div className="text-xs text-gray-400">Expected Profit</div>
              <div className={`font-semibold ${profitColor}`}>
                {suggestion.expected_profit >= 0 ? "+" : ""}{formatCurrency(suggestion.expected_profit)}
                <span className="text-xs ml-1">({profitPercentage}%)</span>
              </div>
            </div>
            <div className="bg-gray-800/50 rounded-lg p-3">
              <div className="text-xs text-gray-400">Time to Profit</div>
              <div className="font-semibold text-white flex items-center gap-1">
                <Clock size={12} />
                {suggestion.time_to_profit}
              </div>
            </div>
          </div>

          {/* Reason with better formatting */}
          <div className="mb-4">
            <div className="text-sm text-gray-300 leading-relaxed bg-gray-800/30 rounded-lg p-3 border-l-4 border-blue-500/50">
              <div className="flex items-start gap-2">
                <Brain size={14} className="text-blue-400 mt-0.5 flex-shrink-0" />
                <div>
                  <strong className="text-blue-300">AI Analysis:</strong> {suggestion.reason}
                </div>
              </div>
            </div>
            {suggestion.category && (
              <div className="mt-2 flex flex-wrap gap-2">
                <span className="px-2 py-1 rounded-full text-xs bg-blue-500/20 text-blue-300 border border-blue-500/30">
                  {suggestion.category.replace(/_/g, " ").toUpperCase()}
                </span>
                {suggestion.confidence_score && (
                  <span className="px-2 py-1 rounded-full text-xs bg-green-500/20 text-green-300 border border-green-500/30">
                    {suggestion.confidence_score}% Confidence
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Enhanced Actions with loading states */}
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => handleAction("buy")}
              disabled={loading}
              className="px-4 py-2 rounded-lg bg-green-500/90 hover:bg-green-500 text-black font-semibold flex items-center gap-2 disabled:opacity-50 transition-all hover:shadow-lg hover:shadow-green-500/20"
            >
              <ShoppingCart size={16} />
              {loading ? "Processing..." : "Mark as Bought"}
            </button>
            <button
              onClick={() => handleAction("watchlist")}
              disabled={loading}
              className="px-4 py-2 rounded-lg bg-blue-500/20 hover:bg-blue-500/30 text-blue-300 border border-blue-500/30 flex items-center gap-2 disabled:opacity-50 transition-all hover:shadow-lg hover:shadow-blue-500/20"
            >
              <Eye size={16} />
              Watchlist
            </button>
            <button
              onClick={() => handleAction("ignore")}
              disabled={loading}
              className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 flex items-center gap-2 disabled:opacity-50 transition-all"
            >
              <X size={16} />
              Dismiss
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});

// Enhanced filter panel with better UX
function FilterPanel({ filters, onChange, onReset }) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  return (
    <div className={cardBase}>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-white flex items-center gap-2">
          <Filter size={18} />
          Suggestion Filters
          <span className="text-xs text-gray-500">({Object.keys(filters).length} active)</span>
        </h3>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="text-sm text-blue-400 hover:text-blue-300 transition-colors"
          >
            {showAdvanced ? "Simple" : "Advanced"}
          </button>
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-1 rounded hover:bg-gray-800 transition-colors"
          >
            <ChevronDown size={16} className={`text-gray-400 transition-transform ${isCollapsed ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>

      {!isCollapsed && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-sm text-gray-300 mb-1">
                Budget <span className="text-gray-500">({filters.budget.toLocaleString()} coins)</span>
              </label>
              <input
                type="range"
                min="10000"
                max="1000000"
                step="10000"
                value={filters.budget}
                onChange={(e) => onChange({ ...filters, budget: Number(e.target.value) })}
                className="w-full mb-2"
              />
              <input
                type="number"
                min="1000"
                step="1000"
                value={filters.budget}
                onChange={(e) => onChange({ ...filters, budget: Number(e.target.value) })}
                className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white text-sm"
              />
            </div>
            <div>
              <label className="block text-sm text-gray-300 mb-1">Risk Tolerance</label>
              <select
                value={filters.risk_tolerance}
                onChange={(e) => onChange({ ...filters, risk_tolerance: e.target.value })}
                className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white"
              >
                <option value="conservative">🟢 Conservative</option>
                <option value="moderate">🟡 Moderate</option>
                <option value="aggressive">🔴 Aggressive</option>
              </select>
            </div>
            <div>
              <label className="block text-sm text-gray-300 mb-1">Time Horizon</label>
              <select
                value={filters.time_horizon}
                onChange={(e) => onChange({ ...filters, time_horizon: e.target.value })}
                className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white"
              >
                <option value="quick_flip">⚡ Quick Flip (1-6h)</option>
                <option value="short">📅 Short Term (6-48h)</option>
                <option value="long_term">📆 Long Term (2-14d)</option>
              </select>
            </div>
          </div>

          {showAdvanced && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t border-gray-700/50">
              <div>
                <label className="block text-sm text-gray-300 mb-2">Strategy Categories</label>
                <div className="space-y-2 max-h-32 overflow-y-auto">
                  {Object.values(BUY_CATEGORIES).map((category) => (
                    <label key={category} className="flex items-center hover:bg-gray-800/30 p-1 rounded">
                      <input
                        type="checkbox"
                        checked={filters.categories?.includes(category)}
                        onChange={(e) => {
                          const categories = filters.categories || [];
                          if (e.target.checked) {
                            onChange({ ...filters, categories: [...categories, category] });
                          } else {
                            onChange({ ...filters, categories: categories.filter(c => c !== category) });
                          }
                        }}
                        className="mr-2 accent-blue-500"
                      />
                      <span className="text-sm text-gray-300">
                        {category.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase())}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-sm text-gray-300 mb-2">Rating Range</label>
                <div className="space-y-2">
                  <div className="flex gap-2">
                    <input
                      type="number"
                      min="40"
                      max="99"
                      value={filters.min_rating}
                      onChange={(e) => onChange({ ...filters, min_rating: Number(e.target.value) })}
                      className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white text-sm"
                      placeholder="Min"
                    />
                    <input
                      type="number"
                      min="40"
                      max="99"
                      value={filters.max_rating}
                      onChange={(e) => onChange({ ...filters, max_rating: Number(e.target.value) })}
                      className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-white text-sm"
                      placeholder="Max"
                    />
                  </div>
                  <div className="text-xs text-gray-500">
                    Current: {filters.min_rating} - {filters.max_rating} OVR
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="flex justify-between items-center mt-4 pt-4 border-t border-gray-700/50">
            <div className="text-xs text-gray-500">
              Filters will auto-update suggestions
            </div>
            <button
              onClick={onReset}
              className="px-4 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm transition-colors"
            >
              Reset All
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default function SmartBuy() {
  const [suggestions, setSuggestions] = useState([]);
  const [marketState, setMarketState] = useState(MARKET_STATES.NORMAL);
  const [marketIntelligence, setMarketIntelligence] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);

  const [filters, setFilters] = useState({
    budget: 100000,
    risk_tolerance: "moderate",
    time_horizon: "short",
    platform: "ps",
    categories: [],
    min_rating: 75,
    max_rating: 95
  });

  // Debounced filter changes to prevent excessive API calls
  const [debouncedFilters, setDebouncedFilters] = useState(filters);
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedFilters(filters);
    }, 500);
    return () => clearTimeout(timer);
  }, [filters]);

  const loadData = useCallback(async (showRefresh = false) => {
    if (showRefresh) setRefreshing(true);
    else setLoading(true);
    setError("");

    try {
      const [suggestionsData, intelligence, userStats] = await Promise.all([
        fetchSmartBuyData(debouncedFilters),
        fetchMarketIntelligence(),
        fetchSuggestionStats()
      ]);

      setSuggestions(suggestionsData.suggestions);
      setMarketState(suggestionsData.market_state);
      setMarketIntelligence(intelligence);
      setStats(userStats);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [debouncedFilters]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Optimized action handlers
  const handleBuyAction = useCallback(async (suggestion) => {
    console.log("User wants to buy:", suggestion);
    // Could integrate with your add-trade functionality
    alert(`Great choice! Remember to buy ${suggestion.name} around ${suggestion.current_price} coins.`);
  }, []);

  const handleWatchlistAction = useCallback(async (suggestion) => {
    try {
      await addWatch({
        player_name: suggestion.name,
        card_id: suggestion.card_id,
        version: suggestion.version || null,
        platform: filters.platform.toUpperCase(),
        notes: `Smart Buy suggestion: Target ${suggestion.target_price} coins`
      });
      alert(`${suggestion.name} added to your watchlist!`);
    } catch (error) {
      alert(`Failed to add to watchlist: ${error.message}`);
    }
  }, [filters.platform]);

  const handleIgnoreAction = useCallback(async (suggestion) => {
    setSuggestions(prev => prev.filter(s => s.card_id !== suggestion.card_id));
  }, []);

  const resetFilters = useCallback(() => {
    setFilters({
      budget: 100000,
      risk_tolerance: "moderate", 
      time_horizon: "short",
      platform: "ps",
      categories: [],
      min_rating: 75,
      max_rating: 95
    });
  }, []);

  // Auto-refresh every 5 minutes
  useEffect(() => {
    const interval = setInterval(() => {
      loadData(true);
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [loadData]);

  if (loading) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-gray-800 rounded w-64" />
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-48 bg-gray-800 rounded-2xl" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <SettingsProvider>
      <div className="p-6 max-w-7xl mx-auto space-y-6">
        {/* Enhanced Header */}
        <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white flex items-center gap-3">
              <Brain size={32} style={{ color: ACCENT }} />
              Smart Buy Suggestions
              <span className="text-sm bg-green-500/20 text-green-300 px-2 py-1 rounded-full border border-green-500/30">
                AI-Powered
              </span>
            </h1>
            <p className="text-gray-400 mt-1 flex items-center gap-2">
              AI-powered trading opportunities based on market analysis
              {lastUpdated && (
                <span className="text-xs text-gray-500">
                  • Last updated {lastUpdated.toLocaleTimeString()}
                </span>
              )}
            </p>
          </div>
          
          <div className="flex items-center gap-3">
            <button
              onClick={() => loadData(true)}
              disabled={refreshing}
              className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-white flex items-center gap-2 disabled:opacity-50 transition-all hover:shadow-lg"
            >
              <RefreshCw size={16} className={refreshing ? "animate-spin" : ""} />
              {refreshing ? "Updating..." : "Refresh"}
            </button>
          </div>
        </div>

        {/* Enhanced Market State & Stats */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <MarketStateIndicator
            state={marketState}
            confidence={marketIntelligence?.current_state_confidence}
            intelligence={marketIntelligence}
          />

          {stats && (
            <>
              {/* Real stat: backend counts actual feedback rows for this */}
              <div className={`${cardBase} bg-purple-400/10 hover:bg-purple-400/15 transition-colors`}>
                <div className="flex items-center gap-3">
                  <Target className="text-purple-400" size={16} />
                  <div>
                    <div className="font-semibold text-purple-400">
                      {stats.suggestions_taken !== null ? stats.suggestions_taken : '—'}
                    </div>
                    <div className="text-xs text-gray-400">Suggestions Taken</div>
                  </div>
                </div>
              </div>

              {/* Backend doesn't compute a real success rate yet - be honest about that
                  instead of showing a permanent 0% (or a fabricated number) that would
                  look like a measured outcome. */}
              <div className={`${cardBase} bg-gray-800/40`}>
                <div className="flex items-center gap-3">
                  <Star className="text-gray-500" size={16} />
                  <div>
                    <div className="font-semibold text-gray-400 text-sm">Not tracked yet</div>
                    <div className="text-xs text-gray-500">Success Rate</div>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Enhanced Filters */}
        <FilterPanel 
          filters={filters} 
          onChange={setFilters}
          onReset={resetFilters}
        />

        {/* Error with retry */}
        {error && (
          <div className="p-4 rounded-lg bg-red-900/20 border border-red-500/30 text-red-300">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle size={16} />
                {error}
              </div>
              <button
                onClick={() => loadData()}
                className="px-3 py-1 rounded bg-red-500/20 hover:bg-red-500/30 text-sm"
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {/* Enhanced Suggestions */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-semibold text-white">
              Buy Recommendations ({suggestions.length})
            </h2>
            {suggestions.length > 0 && (
              <div className="text-sm text-gray-400 flex items-center gap-2">
                <Target size={14} />
                Sorted by priority score
              </div>
            )}
          </div>

          {suggestions.length === 0 ? (
            <div className={`${cardBase} text-center py-12`}>
              <Brain size={48} className="mx-auto mb-4 text-gray-500" />
              <h3 className="text-lg font-semibold text-gray-400 mb-2">No suggestions available</h3>
              <p className="text-gray-500 mb-4">
                Try adjusting your filters or check back later for new opportunities.
              </p>
              <button
                onClick={() => loadData()}
                className="px-4 py-2 rounded-lg bg-blue-500/20 hover:bg-blue-500/30 text-blue-300 border border-blue-500/30"
              >
                Refresh Suggestions
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              {suggestions.map((suggestion) => (
                <SuggestionCard
                  key={suggestion.card_id}
                  suggestion={suggestion}
                  onBuy={handleBuyAction}
                  onWatchlist={handleWatchlistAction}
                  onIgnore={handleIgnoreAction}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </SettingsProvider>
  );
}
