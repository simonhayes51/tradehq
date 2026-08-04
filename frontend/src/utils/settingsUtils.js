import React, { useState, useEffect } from 'react';
import { apiFetch } from '../api/http';
import { BarChart3, DollarSign, Target, Calendar } from 'lucide-react';

// Utility function to format currency based on user settings
export const formatCurrency = (amount, format = 'coins') => {
  if (amount === null || amount === undefined) return 'N/A';
  
  const num = parseInt(amount);
  
  switch (format) {
    case 'abbreviated':
      if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
      if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
      return num.toLocaleString();
    case 'decimal':
      if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
      if (num >= 1000) return `${(num / 1000).toFixed(0)}K`;
      return num.toLocaleString();
    case 'coins':
    default:
      return num.toLocaleString();
  }
};

// Utility function to format dates based on user settings
export const formatDate = (dateString, format = 'US') => {
  if (!dateString) return 'N/A';
  
  const date = new Date(dateString);
  
  switch (format) {
    case 'EU':
      return date.toLocaleDateString('en-GB');
    case 'ISO':
      return date.toISOString().split('T')[0];
    case 'US':
    default:
      return date.toLocaleDateString('en-US');
  }
};

// Data Summary Component for Settings Page
export const DataSummaryCard = () => {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadSummary();
  }, []);

  const loadSummary = async () => {
    try {
      const data = await apiFetch('/api/data/summary');
      setSummary(data);
    } catch (error) {
      console.error('Failed to load data summary:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="border border-gray-200 dark:border-gray-600 rounded-lg p-4">
        <div className="animate-pulse">
          <div className="h-4 bg-gray-200 rounded w-1/3 mb-4"></div>
          <div className="grid grid-cols-2 gap-4">
            <div className="h-16 bg-gray-200 rounded"></div>
            <div className="h-16 bg-gray-200 rounded"></div>
            <div className="h-16 bg-gray-200 rounded"></div>
            <div className="h-16 bg-gray-200 rounded"></div>
          </div>
        </div>
      </div>
    );
  }

  if (!summary) {
    return null;
  }

  const stats = [
    {
      label: 'Total Trades',
      value: summary.trades_count,
      icon: BarChart3,
      color: 'text-blue-600 dark:text-blue-400'
    },
    {
      label: 'Net Profit',
      value: formatCurrency(summary.total_profit),
      icon: DollarSign,
      color: summary.total_profit >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
    },
    {
      label: 'Goals Set',
      value: summary.goals_count,
      icon: Target,
      color: 'text-purple-600 dark:text-purple-400'
    },
    {
      label: 'Days Trading',
      value: summary.earliest_trade && summary.latest_trade 
        ? Math.ceil((new Date(summary.latest_trade) - new Date(summary.earliest_trade)) / (1000 * 60 * 60 * 24)) || 1
        : 0,
      icon: Calendar,
      color: 'text-orange-600 dark:text-orange-400'
    }
  ];

  return (
    <div className="border border-gray-200 dark:border-gray-600 rounded-lg p-4">
      <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-4">Your Trading Data</h4>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat, index) => (
          <div key={index} className="text-center">
            <div className="flex items-center justify-center mb-2">
              <stat.icon className={`h-8 w-8 ${stat.color}`} />
            </div>
            <div className="text-2xl font-bold text-gray-900 dark:text-white">
              {stat.value}
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {stat.label}
            </div>
          </div>
        ))}
      </div>
      
      {summary.earliest_trade && (
        <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-600">
          <div className="text-xs text-gray-500 dark:text-gray-400">
            Trading since: {formatDate(summary.earliest_trade)}
          </div>
          {summary.starting_balance > 0 && (
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              Started with: {formatCurrency(summary.starting_balance)}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

// Settings validation utilities
export const validateSettings = (settings) => {
  const errors = {};
  
  if (!['Console', 'Xbox', 'PC'].includes(settings.default_platform)) {
    errors.default_platform = 'Invalid platform selected';
  }
  
  if (!['coins', 'abbreviated', 'decimal'].includes(settings.currency_format)) {
    errors.currency_format = 'Invalid currency format';
  }
  
  if (!['dark', 'light', 'system'].includes(settings.theme)) {
    errors.theme = 'Invalid theme selected';
  }
  
  if (!['US', 'EU', 'ISO'].includes(settings.date_format)) {
    errors.date_format = 'Invalid date format';
  }
  
  if (!['7d', '30d', '90d', '1y'].includes(settings.default_chart_range)) {
    errors.default_chart_range = 'Invalid chart range';
  }
  
  if (!Array.isArray(settings.custom_tags)) {
    errors.custom_tags = 'Custom tags must be an array';
  } else if (settings.custom_tags.some(tag => typeof tag !== 'string' || tag.length > 50)) {
    errors.custom_tags = 'Invalid custom tags (max 50 characters each)';
  }
  
  if (!Array.isArray(settings.visible_widgets)) {
    errors.visible_widgets = 'Visible widgets must be an array';
  }
  
  return {
    isValid: Object.keys(errors).length === 0,
    errors
  };
};

// Settings change tracker
export const useSettingsTracker = (initialSettings) => {
  const [hasChanges, setHasChanges] = useState(false);
  const [originalSettings, setOriginalSettings] = useState(initialSettings);

  const checkForChanges = (currentSettings) => {
    const changed = JSON.stringify(currentSettings) !== JSON.stringify(originalSettings);
    setHasChanges(changed);
    return changed;
  };

  const resetTracker = (newSettings) => {
    setOriginalSettings(newSettings);
    setHasChanges(false);
  };

  return {
    hasChanges,
    checkForChanges,
    resetTracker
  };
};

// Toast notification helpers
export const showSettingsToast = (type, message) => {
  // This assumes you're using react-hot-toast
  const toast = require('react-hot-toast').toast;
  
  switch (type) {
    case 'success':
      toast.success(message, {
        duration: 3000,
        position: 'top-right',
      });
      break;
    case 'error':
      toast.error(message, {
        duration: 5000,
        position: 'top-right',
      });
      break;
    case 'loading':
      return toast.loading(message, {
        position: 'top-right',
      });
    default:
      toast(message, {
        duration: 3000,
        position: 'top-right',
      });
  }
};

// File validation for import
export const validateImportFile = (file) => {
  const maxSize = 10 * 1024 * 1024; // 10MB
  const allowedTypes = ['application/json', 'text/csv', 'application/vnd.ms-excel'];
  const allowedExtensions = ['.json', '.csv'];
  
  if (!file) {
    return { isValid: false, error: 'No file selected' };
  }
  
  if (file.size > maxSize) {
    return { isValid: false, error: 'File size must be less than 10MB' };
  }
  
  const extension = file.name.toLowerCase().substring(file.name.lastIndexOf('.'));
  if (!allowedExtensions.includes(extension)) {
    return { isValid: false, error: 'Only JSON and CSV files are allowed' };
  }
  
  return { isValid: true, error: null };
};

// Export data helpers
export const downloadFile = (blob, filename) => {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
};

// Theme management utilities
export const applyTheme = (theme) => {
  const root = window.document.documentElement;
  
  switch (theme) {
    case 'dark':
      root.classList.add('dark');
      break;
    case 'light':
      root.classList.remove('dark');
      break;
    case 'system':
      if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
        root.classList.add('dark');
      } else {
        root.classList.remove('dark
