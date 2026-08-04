import React, { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { toast } from 'react-hot-toast';
import { apiFetch } from '../api/http';
import { 
  Download, 
  Upload, 
  Trash2, 
  Save,
  RefreshCw,
  AlertTriangle,
  DollarSign,
  Tag,
  BarChart3,
  Shield,
  FileText,
  CreditCard,
  Bell,
  Clock,
  TrendingUp,
  Target,
  Calculator,
  Eye,
  EyeOff,
  Key
} from 'lucide-react';
import ApiKeysSettings from '../components/ApiKeysSettings.jsx';

const Settings = () => {
  const [settings, setSettings] = useState({
    // Trading preferences
    default_platform: 'Console',
    auto_calculate_tax: true,
    tax_rate: 5,
    profit_display_mode: 'after_tax', // 'before_tax', 'after_tax', 'both'
    default_trade_tag: '',
    quick_tags: ['Snipe', 'Investment', 'Flip', 'SBC'],
    
    // Notifications & Alerts
    browser_notifications: false,
    price_alert_threshold: 10, // % change
    profit_milestone_alerts: true,
    daily_summary_time: '18:00',
    
    // Display & Currency
    currency_display: 'coins', // 'coins', 'k_format', 'millions'
    number_separators: 'commas', // 'commas', 'spaces', 'none'
    show_percentages: true,
    highlight_profitable_trades: true,
    
    // Trading Analysis
    target_profit_margin: 10, // %
    risk_tolerance: 'medium', // 'low', 'medium', 'high'
    investment_horizon: 'short', // 'short', 'medium', 'long'
    track_player_performance: true,
    
    // Privacy & Data
    data_retention_days: 365,
    anonymous_analytics: true,
    backup_frequency: 'weekly', // 'never', 'weekly', 'monthly'
    
    // Advanced Features
    auto_price_updates: true,
    price_update_interval: 5, // minutes
    smart_suggestions: true,
    market_trend_analysis: true
  });

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [newQuickTag, setNewQuickTag] = useState('');
  const [searchParams] = useSearchParams();
  const VALID_SECTIONS = ['trading', 'alerts', 'display', 'analysis', 'data'];
  const [activeSection, setActiveSection] = useState(() => {
    const requested = searchParams.get('section');
    return VALID_SECTIONS.includes(requested) ? requested : 'trading';
  });
  const [hasChanges, setHasChanges] = useState(false);
  const [originalSettings, setOriginalSettings] = useState(null);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    loadSettings();
    loadStats();
  }, []);

  useEffect(() => {
    if (originalSettings) {
      setHasChanges(JSON.stringify(settings) !== JSON.stringify(originalSettings));
    }
  }, [settings, originalSettings]);

  const loadSettings = async () => {
    try {
      setLoading(true);
      const data = await apiFetch('/api/settings');
      setSettings(prev => ({ ...prev, ...data }));
      setOriginalSettings({ ...settings, ...data });
    } catch (error) {
      toast.error('Failed to load settings');
    } finally {
      setLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      const data = await apiFetch('/api/data/summary');
      setStats(data);
    } catch (error) {
      console.error('Failed to load stats');
    }
  };

  const saveSettings = async () => {
    try {
      setSaving(true);
      await apiFetch('/api/settings', {
        method: 'POST',
        body: settings
      });
      setOriginalSettings(settings);
      setHasChanges(false);
      toast.success('Settings saved successfully!');
    } catch (error) {
      toast.error('Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const updateSetting = (key, value) => {
    setSettings(prev => ({ ...prev, [key]: value }));
  };

  const addQuickTag = () => {
    if (newQuickTag.trim() && !settings.quick_tags.includes(newQuickTag.trim())) {
      setSettings(prev => ({
        ...prev,
        quick_tags: [...prev.quick_tags, newQuickTag.trim()]
      }));
      setNewQuickTag('');
    }
  };

  const removeQuickTag = (tagToRemove) => {
    setSettings(prev => ({
      ...prev,
      quick_tags: prev.quick_tags.filter(tag => tag !== tagToRemove)
    }));
  };

  const exportData = async (format = 'json') => {
    try {
      setLoading(true);
      const response = await fetch(`/api/export/trades?format=${format}`, {
        credentials: 'include'
      });
      
      if (!response.ok) throw new Error('Export failed');
      
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `fut-trades-${new Date().toISOString().split('T')[0]}.${format}`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      
      toast.success(`${stats?.trades_count || 0} trades exported as ${format.toUpperCase()}`);
    } catch (error) {
      toast.error('Export failed');
    } finally {
      setLoading(false);
    }
  };

  const importData = async (file) => {
    try {
      setLoading(true);
      const formData = new FormData();
      formData.append('file', file);
      
      const result = await apiFetch('/api/import/trades', {
        method: 'POST',
        body: formData
      });
      
      toast.success(`${result.imported_count} trades imported successfully!`);
      if (result.errors?.length > 0) {
        toast.error(`${result.errors.length} errors occurred during import`);
      }
      loadStats(); // Refresh stats
    } catch (error) {
      toast.error('Import failed: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const deleteAllData = async () => {
    if (deleteConfirm !== 'DELETE') {
      toast.error('Please type DELETE to confirm');
      return;
    }

    try {
      setLoading(true);
      const result = await apiFetch('/api/data/delete-all', {
        method: 'DELETE',
        query: { confirm: true }
      });
      
      toast.success(`All data deleted (${result.details?.trades_deleted || 0} trades removed)`);
      setShowDeleteModal(false);
      setDeleteConfirm('');
      loadStats(); // Refresh stats
    } catch (error) {
      toast.error('Failed to delete data: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const formatCurrency = (amount) => {
    if (!amount) return '0';
    const num = parseInt(amount);
    
    switch (settings.currency_display) {
      case 'k_format':
        if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
        if (num >= 1000) return `${(num / 1000).toFixed(0)}K`;
        return num.toLocaleString();
      case 'millions':
        return `${(num / 1000000).toFixed(2)}M`;
      default:
        return num.toLocaleString();
    }
  };

  const sections = [
    { id: 'trading', label: 'Trading Preferences', icon: BarChart3 },
    { id: 'alerts', label: 'Notifications & Alerts', icon: Bell },
    { id: 'display', label: 'Display & Format', icon: Eye },
    { id: 'analysis', label: 'Trading Analysis', icon: TrendingUp },
    { id: 'data', label: 'Data Management', icon: Shield },
    { id: 'api', label: 'API Access', icon: Key }
  ];

  if (loading && !originalSettings) {
    return (
      <div className="min-h-screen p-6">
        <div className="max-w-6xl mx-auto">
          <div className="flex items-center justify-center py-20">
            <RefreshCw className="animate-spin h-8 w-8 text-blue-500" />
            <span className="ml-3 text-gray-400">Loading settings...</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Settings</h1>
          <p className="text-gray-400">Configure your FUT trading dashboard experience</p>
          
          {stats && (
            <div className="mt-6 grid grid-cols-1 md:grid-cols-4 gap-4">
              <div className="bg-gray-900 rounded-lg p-4 shadow-sm border">
                <div className="flex items-center">
                  <BarChart3 className="h-8 w-8 text-blue-500" />
                  <div className="ml-3">
                    <p className="text-sm font-medium text-gray-400">Total Trades</p>
                    <p className="text-2xl font-semibold text-white">{stats.trades_count}</p>
                  </div>
                </div>
              </div>
              <div className="bg-gray-900 rounded-lg p-4 shadow-sm border">
                <div className="flex items-center">
                  <DollarSign className="h-8 w-8 text-green-500" />
                  <div className="ml-3">
                    <p className="text-sm font-medium text-gray-400">Net Profit</p>
                    <p className={`text-2xl font-semibold ${stats.total_profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {formatCurrency(stats.total_profit)}
                    </p>
                  </div>
                </div>
              </div>
              <div className="bg-gray-900 rounded-lg p-4 shadow-sm border">
                <div className="flex items-center">
                  <FileText className="h-8 w-8 text-orange-500" />
                  <div className="ml-3">
                    <p className="text-sm font-medium text-gray-400">EA Tax Paid</p>
                    <p className="text-2xl font-semibold text-white">{formatCurrency(stats.total_tax)}</p>
                  </div>
                </div>
              </div>
              <div className="bg-gray-900 rounded-lg p-4 shadow-sm border">
                <div className="flex items-center">
                  <Target className="h-8 w-8 text-purple-500" />
                  <div className="ml-3">
                    <p className="text-sm font-medium text-gray-400">Trading Goals</p>
                    <p className="text-2xl font-semibold text-white">{stats.goals_count}</p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
          {/* Sidebar Navigation */}
          <div className="lg:col-span-1">
            <nav className="space-y-1">
              {sections.map((section) => (
                <button
                  key={section.id}
                  onClick={() => setActiveSection(section.id)}
                  className={`w-full flex items-center px-3 py-2 text-sm font-medium rounded-md ${
                    activeSection === section.id
                      ? 'bg-blue-900/30 text-blue-300 border-r-2 border-blue-500'
                      : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                  }`}
                >
                  <section.icon className="h-5 w-5 mr-3" />
                  {section.label}
                </button>
              ))}
            </nav>
          </div>

          {/* Main Content */}
          <div className="lg:col-span-3">
            <div className="bg-gray-900 shadow-sm rounded-lg">
              {/* Trading Preferences */}
              {activeSection === 'trading' && (
                <div className="p-6">
                  <h3 className="text-lg font-medium text-white mb-6">Trading Preferences</h3>
                  
                  <div className="space-y-6">
                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Default Platform</label>
                      <select
                        value={settings.default_platform}
                        onChange={(e) => updateSetting('default_platform', e.target.value)}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="Console">PlayStation (Console)</option>
                        <option value="Xbox">Xbox</option>
                        <option value="PC">PC/Origin</option>
                      </select>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Auto-calculate EA Tax</label>
                        <p className="text-sm text-gray-400">Automatically deduct EA tax from profit calculations</p>
                      </div>
                      <button
                        onClick={() => updateSetting('auto_calculate_tax', !settings.auto_calculate_tax)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.auto_calculate_tax ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.auto_calculate_tax ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">EA Tax Rate (%)</label>
                      <input
                        type="number"
                        min="0"
                        max="10"
                        step="0.1"
                        value={settings.tax_rate}
                        onChange={(e) => updateSetting('tax_rate', parseFloat(e.target.value) || 5)}
                        className="block w-32 px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Profit Display Mode</label>
                      <select
                        value={settings.profit_display_mode}
                        onChange={(e) => updateSetting('profit_display_mode', e.target.value)}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="after_tax">After Tax (Default)</option>
                        <option value="before_tax">Before Tax</option>
                        <option value="both">Show Both</option>
                      </select>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Target Profit Margin (%)</label>
                      <input
                        type="number"
                        min="1"
                        max="100"
                        value={settings.target_profit_margin}
                        onChange={(e) => updateSetting('target_profit_margin', parseInt(e.target.value) || 10)}
                        className="block w-32 px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      />
                      <p className="text-sm text-gray-400 mt-1">Used for trade suggestions and analysis</p>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Quick Tags</label>
                      <div className="flex space-x-2 mb-3">
                        <input
                          type="text"
                          value={newQuickTag}
                          onChange={(e) => setNewQuickTag(e.target.value)}
                          onKeyPress={(e) => e.key === 'Enter' && addQuickTag()}
                          placeholder="Add a quick tag..."
                          className="flex-1 px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                        />
                        <button
                          onClick={addQuickTag}
                          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          <Tag className="h-4 w-4" />
                        </button>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {settings.quick_tags.map((tag) => (
                          <span
                            key={tag}
                            className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-blue-900/40 text-blue-300"
                          >
                            {tag}
                            <button
                              onClick={() => removeQuickTag(tag)}
                              className="ml-2 h-4 w-4 hover:text-blue-600"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Notifications & Alerts */}
              {activeSection === 'alerts' && (
                <div className="p-6">
                  <h3 className="text-lg font-medium text-white mb-6">Notifications & Alerts</h3>
                  
                  <div className="space-y-6">
                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Browser Notifications</label>
                        <p className="text-sm text-gray-400">Get desktop notifications for important events</p>
                      </div>
                      <button
                        onClick={() => updateSetting('browser_notifications', !settings.browser_notifications)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.browser_notifications ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.browser_notifications ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Price Alert Threshold (%)</label>
                      <input
                        type="number"
                        min="1"
                        max="50"
                        value={settings.price_alert_threshold}
                        onChange={(e) => updateSetting('price_alert_threshold', parseInt(e.target.value) || 10)}
                        className="block w-32 px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      />
                      <p className="text-sm text-gray-400 mt-1">Alert when player prices change by this percentage</p>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Profit Milestone Alerts</label>
                        <p className="text-sm text-gray-400">Get notified when you reach profit milestones</p>
                      </div>
                      <button
                        onClick={() => updateSetting('profit_milestone_alerts', !settings.profit_milestone_alerts)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.profit_milestone_alerts ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.profit_milestone_alerts ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Daily Summary Time</label>
                      <input
                        type="time"
                        value={settings.daily_summary_time}
                        onChange={(e) => updateSetting('daily_summary_time', e.target.value)}
                        className="block w-40 px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      />
                      <p className="text-sm text-gray-400 mt-1">When to send daily trading summary</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Display & Format */}
              {activeSection === 'display' && (
                <div className="p-6">
                  <h3 className="text-lg font-medium text-white mb-6">Display & Format</h3>
                  
                  <div className="space-y-6">
                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Currency Display Format</label>
                      <select
                        value={settings.currency_display}
                        onChange={(e) => updateSetting('currency_display', e.target.value)}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="coins">Full Coins (1,250,000)</option>
                        <option value="k_format">K Format (1.25M, 250K)</option>
                        <option value="millions">Millions (1.25M)</option>
                      </select>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Show Percentages</label>
                        <p className="text-sm text-gray-400">Display profit/loss as percentages</p>
                      </div>
                      <button
                        onClick={() => updateSetting('show_percentages', !settings.show_percentages)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.show_percentages ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.show_percentages ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Highlight Profitable Trades</label>
                        <p className="text-sm text-gray-400">Use color coding for profitable vs losing trades</p>
                      </div>
                      <button
                        onClick={() => updateSetting('highlight_profitable_trades', !settings.highlight_profitable_trades)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.highlight_profitable_trades ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.highlight_profitable_trades ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Trading Analysis */}
              {activeSection === 'analysis' && (
                <div className="p-6">
                  <h3 className="text-lg font-medium text-white mb-6">Trading Analysis</h3>
                  
                  <div className="space-y-6">
                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Risk Tolerance</label>
                      <select
                        value={settings.risk_tolerance}
                        onChange={(e) => updateSetting('risk_tolerance', e.target.value)}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="low">Low - Conservative trading</option>
                        <option value="medium">Medium - Balanced approach</option>
                        <option value="high">High - Aggressive trading</option>
                      </select>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Investment Horizon</label>
                      <select
                        value={settings.investment_horizon}
                        onChange={(e) => updateSetting('investment_horizon', e.target.value)}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="short">Short-term (Hours to Days)</option>
                        <option value="medium">Medium-term (Days to Weeks)</option>
                        <option value="long">Long-term (Weeks to Months)</option>
                      </select>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Smart Suggestions</label>
                        <p className="text-sm text-gray-400">Get AI-powered trading suggestions</p>
                      </div>
                      <button
                        onClick={() => updateSetting('smart_suggestions', !settings.smart_suggestions)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.smart_suggestions ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.smart_suggestions ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Market Trend Analysis</label>
                        <p className="text-sm text-gray-400">Enable advanced market trend detection</p>
                      </div>
                      <button
                        onClick={() => updateSetting('market_trend_analysis', !settings.market_trend_analysis)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.market_trend_analysis ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.market_trend_analysis ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Auto Price Updates (Minutes)</label>
                      <select
                        value={settings.price_update_interval}
                        onChange={(e) => updateSetting('price_update_interval', parseInt(e.target.value))}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value={1}>1 minute</option>
                        <option value={5}>5 minutes</option>
                        <option value={10}>10 minutes</option>
                        <option value={30}>30 minutes</option>
                        <option value={60}>1 hour</option>
                      </select>
                    </div>
                  </div>
                </div>
              )}

              {/* Data Management */}
              {activeSection === 'data' && (
                <div className="p-6">
                  <h3 className="text-lg font-medium text-white mb-6">Data Management</h3>
                  
                  <div className="space-y-6">
                    {/* Data Overview */}
                    {stats && (
                      <div className="bg-gray-800 rounded-lg p-4">
                        <h4 className="text-sm font-medium text-white mb-3">Your Trading Data</h4>
                        <div className="grid grid-cols-2 gap-4 text-sm">
                          <div>
                            <span className="text-gray-400">Total Trades:</span>
                            <span className="ml-2 font-medium">{stats.trades_count}</span>
                          </div>
                          <div>
                            <span className="text-gray-400">Trading Goals:</span>
                            <span className="ml-2 font-medium">{stats.goals_count}</span>
                          </div>
                          <div>
                            <span className="text-gray-400">Net Profit:</span>
                            <span className={`ml-2 font-medium ${stats.total_profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                              {formatCurrency(stats.total_profit)}
                            </span>
                          </div>
                          <div>
                            <span className="text-gray-400">EA Tax Paid:</span>
                            <span className="ml-2 font-medium">{formatCurrency(stats.total_tax)}</span>
                          </div>
                        </div>
                        {stats.earliest_trade && (
                          <p className="text-xs text-gray-400 mt-3">
                            Trading since: {new Date(stats.earliest_trade).toLocaleDateString()}
                          </p>
                        )}
                      </div>
                    )}

                    {/* Export Data */}
                    <div className="border border-gray-800 rounded-lg p-4">
                      <div className="flex items-center mb-3">
                        <Download className="h-5 w-5 text-gray-400 mr-2" />
                        <h4 className="text-sm font-medium text-white">Export Trading Data</h4>
                      </div>
                      <p className="text-sm text-gray-400 mb-4">
                        Download your complete trading history for backup or analysis
                      </p>
                      <div className="flex flex-wrap gap-3">
                        <button
                          onClick={() => exportData('csv')}
                          disabled={loading}
                          className="inline-flex items-center px-4 py-2 border border-gray-700 rounded-md shadow-sm bg-gray-900 text-sm font-medium text-gray-200 hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                        >
                          <FileText className="h-4 w-4 mr-2" />
                          Export CSV
                        </button>
                        <button
                          onClick={() => exportData('json')}
                          disabled={loading}
                          className="inline-flex items-center px-4 py-2 border border-gray-700 rounded-md shadow-sm bg-gray-900 text-sm font-medium text-gray-200 hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                        >
                          <FileText className="h-4 w-4 mr-2" />
                          Export JSON
                        </button>
                      </div>
                    </div>

                    {/* Import Data */}
                    <div className="border border-gray-800 rounded-lg p-4">
                      <div className="flex items-center mb-3">
                        <Upload className="h-5 w-5 text-gray-400 mr-2" />
                        <h4 className="text-sm font-medium text-white">Import Trading Data</h4>
                      </div>
                      <p className="text-sm text-gray-400 mb-4">
                        Upload a CSV or JSON file to import your trading history
                      </p>
                      <input
                        type="file"
                        accept=".json,.csv"
                        onChange={(e) => e.target.files[0] && importData(e.target.files[0])}
                        className="block w-full text-sm text-gray-400 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-medium file:bg-blue-900/40 file:text-blue-300 hover:file:bg-blue-900/60"
                      />
                      <p className="text-xs text-gray-400 mt-2">
                        Supports CSV and JSON formats. Duplicate trades will be skipped.
                      </p>
                    </div>

                    {/* Data Retention */}
                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Data Retention Period</label>
                      <select
                        value={settings.data_retention_days}
                        onChange={(e) => updateSetting('data_retention_days', parseInt(e.target.value))}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value={90}>90 days</option>
                        <option value={180}>6 months</option>
                        <option value={365}>1 year</option>
                        <option value={730}>2 years</option>
                        <option value={-1}>Keep forever</option>
                      </select>
                      <p className="text-sm text-gray-400 mt-1">
                        How long to keep your trading data before automatic cleanup
                      </p>
                    </div>

                    {/* Privacy Settings */}
                    <div className="flex items-center justify-between">
                      <div className="flex-1">
                        <label className="block text-sm font-medium text-gray-200">Anonymous Analytics</label>
                        <p className="text-sm text-gray-400">Help improve the platform with anonymous usage data</p>
                      </div>
                      <button
                        onClick={() => updateSetting('anonymous_analytics', !settings.anonymous_analytics)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          settings.anonymous_analytics ? 'bg-blue-600' : 'bg-gray-700'
                        }`}
                      >
                        <span className={`inline-block h-4 w-4 transform rounded-full bg-gray-900 transition-transform ${
                          settings.anonymous_analytics ? 'translate-x-6' : 'translate-x-1'
                        }`} />
                      </button>
                    </div>

                    {/* Backup Frequency */}
                    <div>
                      <label className="block text-sm font-medium text-gray-200 mb-2">Automatic Backup Frequency</label>
                      <select
                        value={settings.backup_frequency}
                        onChange={(e) => updateSetting('backup_frequency', e.target.value)}
                        className="block w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value="never">Never</option>
                        <option value="weekly">Weekly</option>
                        <option value="monthly">Monthly</option>
                      </select>
                      <p className="text-sm text-gray-400 mt-1">
                        Automatically create backups of your data
                      </p>
                    </div>

                    {/* Danger Zone */}
                    <div className="border border-red-900/50 rounded-lg p-4 bg-red-900/20">
                      <div className="flex items-center mb-3">
                        <AlertTriangle className="h-5 w-5 text-red-500 mr-2" />
                        <h4 className="text-sm font-medium text-red-300">Danger Zone</h4>
                      </div>
                      <p className="text-sm text-red-300/90 mb-4">
                        Permanently delete all your trading data. This action cannot be undone.
                      </p>
                      <button
                        onClick={() => setShowDeleteModal(true)}
                        className="inline-flex items-center px-4 py-2 border border-red-800 rounded-md shadow-sm bg-gray-900 text-sm font-medium text-red-300 hover:bg-red-900/30 focus:outline-none focus:ring-2 focus:ring-red-500"
                      >
                        <Trash2 className="h-4 w-4 mr-2" />
                        Delete All Data
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* API Access */}
              {activeSection === 'api' && (
                <div className="p-6">
                  <h3 className="text-lg font-medium text-white mb-6">API Access</h3>
                  <ApiKeysSettings />
                </div>
              )}

              {/* Save Button - Always visible at bottom */}
              <div className="border-t border-gray-800 px-6 py-4 bg-gray-800 rounded-b-lg">
                <div className="flex justify-between items-center">
                  <div className="flex items-center space-x-4">
                    <button
                      onClick={loadSettings}
                      disabled={loading}
                      className="inline-flex items-center px-3 py-2 text-sm font-medium text-gray-400 hover:text-gray-100 disabled:opacity-50"
                    >
                      <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
                      Reset Changes
                    </button>
                    {hasChanges && (
                      <span className="text-sm text-amber-600 font-medium">
                        You have unsaved changes
                      </span>
                    )}
                  </div>
                  <button
                    onClick={saveSettings}
                    disabled={saving || loading || !hasChanges}
                    className="inline-flex items-center px-6 py-2 border border-transparent rounded-md shadow-sm bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {saving ? (
                      <RefreshCw className="animate-spin h-4 w-4 mr-2" />
                    ) : (
                      <Save className="h-4 w-4 mr-2" />
                    )}
                    {hasChanges ? 'Save Settings' : 'No Changes'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Delete Confirmation Modal */}
        {showDeleteModal && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
            <div className="bg-gray-900 rounded-lg max-w-md w-full p-6">
              <div className="flex items-center mb-4">
                <AlertTriangle className="h-6 w-6 text-red-500 mr-3" />
                <h3 className="text-lg font-medium text-white">Delete All Trading Data</h3>
              </div>
              <div className="mb-4">
                <p className="text-sm text-gray-400 mb-2">
                  This will permanently delete:
                </p>
                <ul className="text-sm text-gray-400 list-disc list-inside space-y-1">
                  <li>All {stats?.trades_count || 0} trades</li>
                  <li>All {stats?.goals_count || 0} trading goals</li>
                  <li>Portfolio balance and settings</li>
                  <li>All historical data and analytics</li>
                </ul>
              </div>
              <p className="text-sm text-gray-200 mb-4">
                Type <span className="font-mono font-bold bg-gray-800 text-gray-100 px-2 py-1 rounded">DELETE</span> to confirm:
              </p>
              <input
                type="text"
                value={deleteConfirm}
                onChange={(e) => setDeleteConfirm(e.target.value)}
                className="w-full px-3 py-2 border border-gray-700 rounded-md shadow-sm focus:outline-none focus:ring-red-500 focus:border-red-500 mb-4"
                placeholder="Type DELETE"
              />
              <div className="flex space-x-3">
                <button
                  onClick={() => {
                    setShowDeleteModal(false);
                    setDeleteConfirm('');
                  }}
                  className="flex-1 px-4 py-2 text-sm font-medium text-gray-200 bg-gray-900 border border-gray-700 rounded-md hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  Cancel
                </button>
                <button
                  onClick={deleteAllData}
                  disabled={loading || deleteConfirm !== 'DELETE'}
                  className="flex-1 px-4 py-2 text-sm font-medium text-white bg-red-600 border border-transparent rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50"
                >
                  {loading ? (
                    <RefreshCw className="animate-spin h-4 w-4 mx-auto" />
                  ) : (
                    'Delete All Data'
                  )}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Settings;
