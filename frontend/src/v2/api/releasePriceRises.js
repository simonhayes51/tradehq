// src/v2/api/releasePriceRises.js
//
// FC26 early-release -> full-release price leaderboard. See
// app/routers/v2/release_price_rises.py for the underlying query and its
// wording-discipline notes (association, not causation).
import { api } from "../lib/api";

export async function getReleasePriceRises(params = {}) {
  const { data } = await api.get("/api/v2/release-price-rises", { params });
  return data;
}
