export const getAdminIds = () => {
  const raw = import.meta.env.VITE_ADMIN_IDS || "";
  return raw
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
};

export const isAdminUser = (user) => {
  if (!user) return false;
  if (user.is_admin || user.role === "admin") return true;
  const adminIds = getAdminIds();
  if (adminIds.length === 0) return false;
  const userId = String(user.user_id || user.id || "");
  const username = String(user.username || "");
  const globalName = String(user.global_name || "");
  const tag = String(user.tag || "");
  const normalized = adminIds.map((id) => id.toLowerCase());
  return (
    normalized.includes(userId.toLowerCase()) ||
    (username && normalized.includes(username.toLowerCase())) ||
    (globalName && normalized.includes(globalName.toLowerCase())) ||
    (tag && normalized.includes(tag.toLowerCase()))
  );
};
