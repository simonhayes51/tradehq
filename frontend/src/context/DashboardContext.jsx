// context/DashboardContext.jsx
import { createContext, useContext, useReducer, useEffect } from 'react';
import api from '../axios'; // ✅ Fixed import path

const DashboardContext = createContext();

const dashboardReducer = (state, action) => {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, isLoading: action.payload };
    case 'SET_DASHBOARD_DATA':
      return { ...state, ...action.payload, isLoading: false, error: null };
    case 'SET_ERROR':
      return { ...state, error: action.payload, isLoading: false };
    case 'ADD_TRADE':
      return {
        ...state,
        trades: [action.payload, ...state.trades.slice(0, 9)], // keep latest 10
        netProfit: state.netProfit + (action.payload.profit || 0),
        taxPaid: state.taxPaid + (action.payload.ea_tax || 0),
        profile: {
          ...state.profile,
          totalProfit: state.profile.totalProfit + (action.payload.profit || 0),
          tradesLogged: state.profile.tradesLogged + 1,
        },
      };
    case 'REFRESH_DATA':
      return { ...state, shouldRefresh: true };
    default:
      return state;
  }
};

const initialState = {
  isLoading: true,
  error: null,
  netProfit: 0,
  taxPaid: 0,
  startingBalance: 0,
  trades: [],
  profile: {
    totalProfit: 0,
    tradesLogged: 0,
    winRate: 0,
    mostUsedTag: 'N/A',
    bestTrade: null,
  },
  shouldRefresh: false,
};

export const DashboardProvider = ({ children }) => {
  const [state, dispatch] = useReducer(dashboardReducer, initialState);

  const fetchDashboardData = async () => {
    try {
      dispatch({ type: 'SET_LOADING', payload: true });
      const response = await api.get('/api/dashboard');
      dispatch({ type: 'SET_DASHBOARD_DATA', payload: response.data });
    } catch (error) {
      console.error('Dashboard fetch error:', error);
      dispatch({
        type: 'SET_ERROR',
        payload: error.userMessage || 'Failed to load dashboard data',
      });
    }
  };

  // Ensure new trades have a trade_id and numeric fields (so delete/edit work)
  const addTrade = async (tradeData) => {
    try {
      const trade_id = Date.now(); // client-generated id, used by backend & UI
      const payload = {
        ...tradeData,
        trade_id,
        buy: Number(tradeData.buy || 0),
        sell: Number(tradeData.sell || 0),
        quantity: Number(tradeData.quantity || 1),
      };

      const response = await api.post('/api/trades', payload);

      const newTrade = {
        ...payload,
        profit: response.data.profit,
        ea_tax: response.data.ea_tax,
        timestamp: new Date().toISOString(),
      };

      dispatch({ type: 'ADD_TRADE', payload: newTrade });
      return { success: true, message: response.data.message };
    } catch (error) {
      console.error('Add trade error:', error);
      return {
        success: false,
        message: error.userMessage || 'Failed to add trade',
      };
    }
  };

  const getAllTrades = async () => {
    try {
      const response = await api.get('/api/trades');
      return { success: true, trades: response.data.trades };
    } catch (error) {
      console.error('Get trades error:', error);
      return {
        success: false,
        message: error.userMessage || 'Failed to load trades',
      };
    }
  };

  const deleteTrade = async (tradeId) => {
    try {
      await api.delete(`/api/trades/${tradeId}`);
      await fetchDashboardData();
      return { success: true, message: 'Trade deleted successfully' };
    } catch (error) {
      console.error('Delete trade error:', error);
      return {
        success: false,
        message: error.userMessage || 'Failed to delete trade',
      };
    }
  };

  const refreshData = () => fetchDashboardData();

  useEffect(() => {
    fetchDashboardData();
  }, []);

  useEffect(() => {
    if (state.shouldRefresh) fetchDashboardData();
  }, [state.shouldRefresh]);

  const value = {
    ...state,
    addTrade,
    getAllTrades,
    deleteTrade,
    refreshData,
    fetchDashboardData,
  };

  return (
    <DashboardContext.Provider value={value}>
      {children}
    </DashboardContext.Provider>
  );
};

export const useDashboard = () => {
  const context = useContext(DashboardContext);
  if (!context) {
    throw new Error('useDashboard must be used within a DashboardProvider');
  }
  return context;
};
