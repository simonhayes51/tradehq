// src/api/players.js
import api from "../axios";

export const searchPlayers = async (q) => {
  if (!q || !q.trim()) return [];
  const res = await api.get("/api/search-players", { params: { q } });
  return res.data.players || [];
};
