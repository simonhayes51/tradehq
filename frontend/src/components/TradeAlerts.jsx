import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bell, X, TrendingUp, TrendingDown, AlertCircle, CheckCircle } from 'lucide-react';
import api from '../axios';
import toast from 'react-hot-toast';

const TradeAlerts = () => {
  const [alerts, setAlerts] = useState([]);
  const [isOpen, setIsOpen] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    // Connect to WebSocket for real-time alerts
    const connectWebSocket = () => {
      const ws = new WebSocket('wss://api.futhub.co.uk/ws/alerts');

      ws.onopen = () => {
        console.log('WebSocket connected');
      };

      ws.onmessage = (event) => {
        const alert = JSON.parse(event.data);
        handleNewAlert(alert);
      };

      ws.onerror = (error) => {
        console.error('WebSocket error:', error);
      };

      ws.onclose = () => {
        console.log('WebSocket disconnected, reconnecting...');
        setTimeout(connectWebSocket, 3000); // Reconnect after 3 seconds
      };

      return ws;
    };

    const ws = connectWebSocket();
    fetchRecentAlerts();

    return () => {
      ws.close();
    };
  }, []);

  const fetchRecentAlerts = async () => {
    try {
      const { data } = await api.get('/api/alerts/recent');
      setAlerts(data.alerts || []);
      setUnreadCount(data.unreadCount || 0);
    } catch (error) {
      console.error('Failed to fetch alerts:', error);
    }
  };

  const handleNewAlert = (alert) => {
    setAlerts(prev => [alert, ...prev].slice(0, 20)); // Keep last 20 alerts
    setUnreadCount(prev => prev + 1);

    // Show toast notification
    const icon = getAlertIcon(alert.type);
    toast.custom((t) => (
      <motion.div
        initial={{ opacity: 0, y: -50 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        className="bg-slate-900 border border-white/10 rounded-xl p-4 shadow-xl max-w-sm"
      >
        <div className="flex items-start gap-3">
          <div className={getAlertColor(alert.type)}>
            {icon}
          </div>
          <div className="flex-1">
            <p className="font-bold text-white">{alert.title}</p>
            <p className="text-sm text-slate-400">{alert.message}</p>
          </div>
          <button onClick={() => toast.dismiss(t.id)} className="text-slate-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
      </motion.div>
    ), {
      duration: 5000,
      position: 'top-right'
    });
  };

  const markAsRead = async (alertId) => {
    try {
      await api.post(`/api/alerts/${alertId}/read`);
      setAlerts(prev => prev.map(a => a.id === alertId ? { ...a, read: true } : a));
      setUnreadCount(prev => Math.max(0, prev - 1));
    } catch (error) {
      console.error('Failed to mark alert as read:', error);
    }
  };

  const markAllAsRead = async () => {
    try {
      await api.post('/api/alerts/read-all');
      setAlerts(prev => prev.map(a => ({ ...a, read: true })));
      setUnreadCount(0);
    } catch (error) {
      toast.error('Failed to mark all as read');
    }
  };

  const getAlertIcon = (type) => {
    switch (type) {
      case 'price_spike':
        return <TrendingUp className="w-5 h-5" />;
      case 'price_drop':
        return <TrendingDown className="w-5 h-5" />;
      case 'trade_opportunity':
        return <AlertCircle className="w-5 h-5" />;
      case 'goal_reached':
        return <CheckCircle className="w-5 h-5" />;
      default:
        return <Bell className="w-5 h-5" />;
    }
  };

  const getAlertColor = (type) => {
    switch (type) {
      case 'price_spike':
        return 'text-green-400';
      case 'price_drop':
        return 'text-red-400';
      case 'trade_opportunity':
        return 'text-purple-400';
      case 'goal_reached':
        return 'text-blue-400';
      default:
        return 'text-slate-400';
    }
  };

  const getAlertBg = (type) => {
    switch (type) {
      case 'price_spike':
        return 'bg-green-400/10 border-green-500/30';
      case 'price_drop':
        return 'bg-red-400/10 border-red-500/30';
      case 'trade_opportunity':
        return 'bg-purple-400/10 border-purple-500/30';
      case 'goal_reached':
        return 'bg-blue-400/10 border-blue-500/30';
      default:
        return 'bg-slate-800/30 border-white/5';
    }
  };

  return (
    <>
      {/* Bell Icon */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative p-2 hover:bg-slate-800/50 rounded-lg transition-all"
      >
        <Bell className="w-6 h-6 text-slate-300" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 w-5 h-5 bg-red-500 rounded-full text-xs font-bold flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Alerts Panel */}
      <AnimatePresence>
        {isOpen && (
          <>
            {/* Backdrop */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsOpen(false)}
              className="fixed inset-0 bg-black/50 z-40"
            />

            {/* Panel */}
            <motion.div
              initial={{ opacity: 0, x: 300 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 300 }}
              className="fixed right-0 top-0 bottom-0 w-full max-w-md bg-[#0e1320] border-l border-white/10 z-50 overflow-y-auto"
            >
              {/* Header */}
              <div className="sticky top-0 bg-slate-900/95 backdrop-blur-lg p-4 border-b border-white/10 flex items-center justify-between">
                <div>
                  <h2 className="text-xl font-black">Alerts</h2>
                  <p className="text-sm text-slate-400">{unreadCount} unread</p>
                </div>
                <div className="flex items-center gap-2">
                  {unreadCount > 0 && (
                    <button
                      onClick={markAllAsRead}
                      className="text-sm text-purple-400 hover:text-purple-300 font-medium"
                    >
                      Mark all read
                    </button>
                  )}
                  <button
                    onClick={() => setIsOpen(false)}
                    className="p-2 hover:bg-slate-800/50 rounded-lg"
                  >
                    <X className="w-5 h-5" />
                  </button>
                </div>
              </div>

              {/* Alerts List */}
              <div className="p-4 space-y-3">
                {alerts.length === 0 ? (
                  <div className="text-center py-12">
                    <Bell className="w-12 h-12 text-slate-600 mx-auto mb-3" />
                    <p className="text-slate-400">No alerts yet</p>
                    <p className="text-sm text-slate-500 mt-1">
                      We'll notify you about price changes and trading opportunities
                    </p>
                  </div>
                ) : (
                  alerts.map((alert) => (
                    <motion.div
                      key={alert.id}
                      initial={{ opacity: 0, y: -10 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={`rounded-xl p-4 border cursor-pointer transition-all ${
                        alert.read ? 'bg-slate-900/30 border-white/5' : getAlertBg(alert.type)
                      }`}
                      onClick={() => !alert.read && markAsRead(alert.id)}
                    >
                      <div className="flex items-start gap-3">
                        <div className={getAlertColor(alert.type)}>
                          {getAlertIcon(alert.type)}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-start justify-between mb-1">
                            <p className="font-bold">{alert.title}</p>
                            {!alert.read && (
                              <div className="w-2 h-2 bg-purple-500 rounded-full" />
                            )}
                          </div>
                          <p className="text-sm text-slate-400 mb-2">{alert.message}</p>
                          {alert.data && (
                            <div className="text-xs text-slate-500">
                              {alert.data.player && `Player: ${alert.data.player}`}
                              {alert.data.priceChange && ` • ${alert.data.priceChange >= 0 ? '+' : ''}${alert.data.priceChange}%`}
                            </div>
                          )}
                          <p className="text-xs text-slate-500 mt-2">
                            {new Date(alert.timestamp).toLocaleString()}
                          </p>
                        </div>
                      </div>
                    </motion.div>
                  ))
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
};

export default TradeAlerts;
