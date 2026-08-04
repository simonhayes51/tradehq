// src/api/smartBuy.js
import { apiFetch } from "./http";

const API_BASE = import.meta.env.VITE_API_URL || "";

/**
 * Market state classification for smart buy suggestions
 */
export const MARKET_STATES = {
  NORMAL: "normal",
  PRE_CRASH: "pre_crash", 
  CRASH_ACTIVE: "crash_active",
  RECOVERY: "recovery",
  PROMO_HYPE: "promo_hype"
};

/**
 * Buy suggestion categories
 */
export const BUY_CATEGORIES = {
  CRASH_ANTICIPATION: "crash_anticipation",
  RECOVERY_PLAYS: "recovery_plays", 
  TREND_MOMENTUM: "trend_momentum",
  VALUE_ARBITRAGE: "value_arbitrage"
};

/**
 * Fetch smart buy suggestions based on user criteria
 */
export async function fetchSmartBuyData(params = {}) {
  const {
    budget = 100000,
    risk_tolerance = "moderate", // conservative, moderate, aggressive
    time_horizon = "short", // quick_flip, short, long_term
    platform = "ps",
    categories = [], // array of BUY_CATEGORIES
    exclude_positions = [],
    min_rating = 75,
    max_rating = 95,
    preferred_leagues = [],
    preferred_nations = []
  } = params;

  const query = {
    budget,
    risk_tolerance,
    time_horizon, 
    platform,
    categories: categories.length ? categories.join(",") : undefined,
    exclude_positions: exclude_positions.length ? exclude_positions.join(",") : undefined,
    min_rating,
    max_rating,
    preferred_leagues: preferred_leagues.length ? preferred_leagues.join(",") : undefined,
    preferred_nations: preferred_nations.length ? preferred_nations.join(",") : undefined
  };

  try {
    const data = await apiFetch("/api/smart-buy/suggestions", { query });
    return {
      suggestions: data.suggestions || [],
      market_state: data.market_state || MARKET_STATES.NORMAL,
      market_analysis: data.market_analysis || {},
      next_update: data.next_update,
      confidence_score: data.confidence_score || 0
    };
  } catch (error) {
    console.error("Smart buy suggestions failed:", error);
    throw new Error(`Failed to fetch suggestions: ${error.message}`);
  }
}

/**
 * Get detailed analysis for a specific buy suggestion
 */
export async function fetchSuggestionDetail(cardId, platform = "ps") {
  try {
    const data = await apiFetch(`/api/smart-buy/suggestion/${cardId}`, {
      query: { platform }
    });
    
    return {
      card_id: data.card_id,
      analysis: data.analysis || {},
      price_history: data.price_history || [],
      similar_cards: data.similar_cards || [],
      risk_factors: data.risk_factors || [],
      profit_scenarios: data.profit_scenarios || {}
    };
  } catch (error) {
    console.error("Suggestion detail failed:", error);
    throw new Error(`Failed to fetch suggestion detail: ${error.message}`);
  }
}

/**
 * Get current market state and upcoming events
 */
export async function fetchMarketIntelligence() {
  try {
    const data = await apiFetch("/api/smart-buy/market-intelligence");
    
    return {
      current_state: data.current_state || MARKET_STATES.NORMAL,
      upcoming_events: data.upcoming_events || [],
      crash_probability: data.crash_probability || 0,
      recovery_indicators: data.recovery_indicators || {},
      whale_activity: data.whale_activity || [],
      meta_shifts: data.meta_shifts || []
    };
  } catch (error) {
    console.error("Market intelligence failed:", error);
    throw new Error(`Failed to fetch market intelligence: ${error.message}`);
  }
}

/**
 * Submit feedback on a suggestion (bought, ignored, etc.)
 */
export async function submitSuggestionFeedback(cardId, action, notes = "") {
  try {
    await apiFetch("/api/smart-buy/feedback", {
      method: "POST",
      body: {
        card_id: cardId,
        action, // "bought", "ignored", "watchlisted"
        notes,
        timestamp: new Date().toISOString()
      }
    });
    return { success: true };
  } catch (error) {
    console.error("Suggestion feedback failed:", error);
    throw new Error(`Failed to submit feedback: ${error.message}`);
  }
}

/**
 * Get user's suggestion performance stats
 */
export async function fetchSuggestionStats() {
  try {
    const data = await apiFetch("/api/smart-buy/stats");
    
    return {
      total_suggestions: data.total_suggestions || 0,
      suggestions_taken: data.suggestions_taken || 0,
      success_rate: data.success_rate || 0,
      avg_profit: data.avg_profit || 0,
      total_profit: data.total_profit || 0,
      category_performance: data.category_performance || {}
    };
  } catch (error) {
    console.error("Suggestion stats failed:", error);
    return {
      total_suggestions: 0,
      suggestions_taken: 0, 
      success_rate: 0,
      avg_profit: 0,
      total_profit: 0,
      category_performance: {}
    };
  }
}
