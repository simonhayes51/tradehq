import { createContext, useContext, useReducer, useEffect } from 'react';
import api from '../axios';
import { queryClient } from '../lib/queryClient';

const AuthContext = createContext();

// Same convention as src/axios.js's own DEV-gated logging - these fire on
// every page load for every visitor, so they stay out of the production
// console entirely rather than leaking auth-flow internals as noise.
const devLog = (...args) => { if (import.meta.env.DEV) console.log(...args); };

const authReducer = (state, action) => {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, isLoading: action.payload };
    case 'SET_AUTHENTICATED':
      return {
        ...state,
        isAuthenticated: true,
        user: action.payload,
        isLoading: false,
        error: null
      };
    case 'SET_UNAUTHENTICATED':
      return {
        ...state,
        isAuthenticated: false,
        user: null,
        isLoading: false,
        error: null
      };
    case 'SET_ERROR':
      return {
        ...state,
        error: action.payload,
        isLoading: false
      };
    default:
      return state;
  }
};

const initialState = {
  isAuthenticated: false,
  user: null,
  isLoading: true,
  error: null
};

export const AuthProvider = ({ children }) => {
  const [state, dispatch] = useReducer(authReducer, initialState);

  const checkAuthStatus = async () => {
    try {
      devLog('🔍 Checking auth status...');
      dispatch({ type: 'SET_LOADING', payload: true });
      const response = await api.get('/api/me');
      
      if (response.data.authenticated) {
        devLog('✅ User authenticated:', response.data.user_id);
        dispatch({ 
          type: 'SET_AUTHENTICATED', 
          payload: response.data 
        });
      } else {
        devLog('❌ User not authenticated, reason:', response.data.error);
        dispatch({ type: 'SET_UNAUTHENTICATED' });
      }
    } catch (error) {
      devLog('❌ Auth check failed:', error.message);
      dispatch({ type: 'SET_UNAUTHENTICATED' });
    }
  };

  // returnTo: a same-app hash-route path ("/players/34567") to send the
  // user back to once Discord OAuth completes, so clicking "Watch" on a
  // shared card while signed out doesn't strand them on Home afterwards.
  // Defaults to wherever they currently are. Backend re-validates this
  // (_safe_return_to in main.py) rather than trusting it blindly.
  const login = (returnTo) => {
    const path = returnTo || window.location.hash.slice(1) || "/";
    window.location.href = `${api.defaults.baseURL}/api/login?return_to=${encodeURIComponent(path)}`;
  };

  const logout = async () => {
    try {
      await api.get('/api/logout');
    } catch (error) {
      console.error('Logout error:', error);
    } finally {
      // Clear the cache BEFORE flipping to unauthenticated, and do it
      // whether or not the server call succeeded.
      //
      // Without this the previous user's watchlist, portfolio, alerts and
      // unredacted Elite opportunities stayed in the react-query cache in
      // memory. On a shared machine the next person to sign in would be
      // shown them - react-query keys are not scoped by user, so a cache
      // hit is a cache hit. `clear()` rather than `invalidateQueries()`
      // because invalidation keeps the stale data and merely refetches:
      // it would still be rendered in the meantime.
      queryClient.clear();
      dispatch({ type: 'SET_UNAUTHENTICATED' });
    }
  };

  useEffect(() => {
    devLog('🔍 AuthProvider mounted');
    devLog('🔍 Current pathname:', window.location.pathname);
    devLog('🔍 Current hash:', window.location.hash);
    
    // CRITICAL: Don't run auth checks if we're on /access-denied
    if (window.location.pathname === '/access-denied' || 
        window.location.pathname.includes('access-denied')) {
      devLog('🔍 On access-denied page, skipping all auth checks');
      dispatch({ type: 'SET_LOADING', payload: false });
      return;
    }
    
    // Only check auth for other routes
    checkAuthStatus();
  }, []);

  const value = {
    ...state,
    login,
    logout,
    checkAuthStatus
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
