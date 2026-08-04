// src/utils/pwa.js
// PWA helpers only. (A half-pasted React "TradingGoals" component used to
// live at the bottom of this file with no imports - it referenced useState
// and api that were never imported, so it would have crashed the moment
// anything rendered it. Removed; the real component is
// src/components/TradingGoals.jsx.)

export const registerSW = () => {
  if (!("serviceWorker" in navigator)) return;

  // Visitors with the OLD service worker (cache-first forever, no
  // versioning - see public/sw.js) already have it installed and serving
  // stale builds. The new one calls skipWaiting()/clients.claim(), so as
  // soon as the browser finishes installing it in the background,
  // 'controllerchange' fires - reload once, automatically, so people don't
  // need to know to hard-refresh to escape a broken cache they can't see.
  let reloadedForUpdate = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloadedForUpdate) return;
    reloadedForUpdate = true;
    window.location.reload();
  });

  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // SW is progressive enhancement - never break the app over it.
    });
  });
};

export const requestNotificationPermission = async () => {
  if ("Notification" in window) {
    const permission = await Notification.requestPermission();
    return permission === "granted";
  }
  return false;
};

export const sendNotification = (title, options = {}) => {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, {
      icon: "/icons/icon-192x192.png",
      badge: "/icons/icon-72x72.png",
      ...options,
    });
  }
};
