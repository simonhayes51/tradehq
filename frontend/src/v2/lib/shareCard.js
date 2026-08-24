// src/v2/lib/shareCard.js
//
// Shared download/copy actions for the two share-card flows (a single
// trade result, and a period recap) - both endpoints return the same
// { image_data_url, caption } shape, so the client-side handling is
// identical either way.

export async function downloadDataUrlAsFile(dataUrl, filename) {
  const blob = await (await fetch(dataUrl)).blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// Copies the actual PNG bytes to the clipboard (not just a link), so the
// user can paste directly into Discord's message box. Requires the
// async Clipboard API + ClipboardItem, which not every browser supports
// (Safari and Firefox lag Chrome here) - callers should catch and fall
// back to "download instead" messaging.
export async function copyImageToClipboard(dataUrl) {
  if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
    throw new Error("Your browser doesn't support copying images - use Download instead.");
  }
  const blob = await (await fetch(dataUrl)).blob();
  await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
}

export async function copyText(text) {
  if (!text || !navigator.clipboard?.writeText) return;
  try { await navigator.clipboard.writeText(text); } catch { /* clipboard permission denied */ }
}

export function slugFilename(name) {
  return String(name || "trade").replace(/[^a-z0-9]+/gi, "-").toLowerCase();
}
