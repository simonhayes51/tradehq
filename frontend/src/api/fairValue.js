// src/api/fairValue.js
// Fair Value engine endpoints. All go through the shared axios instance
// (cookies, retries, 402 -> premium:blocked event).
import api from "../axios";

export async function getFairValue(cardId) {
  const { data } = await api.get(`/api/market/fair-value/${cardId}`);
  return data;
}

export async function getUndervalued(params = {}) {
  const { data } = await api.get("/api/market/undervalued", { params });
  return data;
}

export async function getUndervaluedTeaser() {
  const { data } = await api.get("/api/market/undervalued/teaser");
  return data;
}

export async function getAnomalies(params = {}) {
  const { data } = await api.get("/api/market/anomalies", { params });
  return data;
}
