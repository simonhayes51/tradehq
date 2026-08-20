// src/api/account.js
import api from "../axios";

// Destructive - see backend/main.py's delete_account() for exactly what
// this does and does not remove (billing history is retained where the
// user has ever subscribed, per the subscriptions->user_profiles foreign
// key that makes a hard delete unsafe in that case).
export async function deleteAccount() {
  const { data } = await api.delete("/api/account?confirm=true");
  return data;
}
