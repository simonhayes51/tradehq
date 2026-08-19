// src/list-badges.js
//
// Per-row BUY/AVOID markers for an unfiltered Transfer Market search
// (many different cards in one response - Rice, Berger, Saliba, etc. all
// on one page), on top of the existing single-card overlay (which only
// ever handled a search scoped to ONE exact card). Requested after the
// overlay was already confirmed working end-to-end.
//
// KNOWN LIMITATION, READ BEFORE CHANGING THE MATCHING LOGIC
// -------------------------------------------------------------------
// This project has never had access to EA's actual Transfer Market
// results DOM (only its network responses) - every other file reasons
// entirely from parsed response bodies and never queries the page DOM
// at all. Anchoring a badge to a specific on-screen row necessarily
// breaks that pattern: it has to find, in the live DOM, the same price
// EA already rendered for a given auction. findPriceAnchor() below does
// this by text-matching the formatted "Buy Now" price - a reasonable
// first pass, but genuinely unverified against EA's real markup, and
// likely to need correction once tested live (matches the earlier
// overlay's own history - see README.md).
//
// WHY BADGES ARE POSITIONED, NOT INSERTED
// -------------------------------------------------------------------
// A badge is a small `position: fixed` element placed at the matched
// price node's on-screen coordinates, appended to this module's OWN
// container - never inserted into EA's row itself. This means a wrong
// or fragile match degrades to "a badge floats in a slightly wrong
// spot", never to "EA's own click handling/layout broke" - the row
// underneath is completely untouched either way, which is the same
// non-interference guarantee every other file in this project holds
// for network requests.
import { getCardIntelligence } from "./api-client.js";
import { combineIntelligence } from "./market-calc.js";
import { render as renderOverlay } from "./overlay.js";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
const REPOSITION_INTERVAL_MS = 250;

let containerEl = null;
// auctionKey -> { el, auction, anchorNode, verdict }
const badges = new Map();
let repositionTimer = null;

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
}

function ensureContainer() {
  if (containerEl && containerEl.isConnected) return containerEl;
  containerEl = document.createElement("div");
  containerEl.id = "eafc-live-list-badges-root";
  containerEl.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483646;";
  (document.body || document.documentElement).appendChild(containerEl);
  return containerEl;
}

export function auctionKey(auction) {
  return auction.tradeId != null ? `t:${auction.tradeId}` : `c:${auction.sourceCardId}:${auction.buyNowPrice}`;
}

/**
 * Finds the DOM text node showing this auction's exact "Buy Now" price,
 * preferring one near a "Buy Now" label to avoid matching an identical
 * Start Price/Bid number. Falls back to any exact match if no
 * "Buy Now"-qualified one is found. Returns null if nothing matches -
 * the caller simply skips that auction's badge rather than guessing.
 */
function findPriceAnchor(priceText) {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.nodeValue && node.nodeValue.trim() === priceText
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_SKIP;
    },
  });

  const candidates = [];
  let node;
  while ((node = walker.nextNode())) {
    if (containerEl && containerEl.contains(node)) continue; // never match our own badges
    candidates.push(node);
  }
  if (candidates.length === 0) return null;

  for (const candidate of candidates) {
    let el = candidate.parentElement;
    for (let depth = 0; el && depth < 6; depth++, el = el.parentElement) {
      const text = el.textContent || "";
      if (/buy\s*now/i.test(text)) return candidate;
    }
  }
  return candidates[0];
}

export function verdictFor(auction, intelligence) {
  if (!intelligence) return null;
  const combined = combineIntelligence([auction], intelligence);
  if (combined.reason === "confirmed") return { tone: "buy", label: "BUY" };
  if (intelligence.signal === "avoid") return { tone: "avoid", label: "AVOID" };
  return null; // neutral/insufficient-data rows stay unbadged, same
  // philosophy as the single-card overlay: never manufacture a signal
  // that isn't there.
}

function makeBadgeEl(verdict, onClick) {
  const el = document.createElement("button");
  el.type = "button";
  el.textContent = verdict.label === "BUY" ? "✓ BUY" : "⚠ AVOID";
  const bg = verdict.tone === "buy" ? "#123d24" : "#3d1414";
  const fg = verdict.tone === "buy" ? "#5fd98a" : "#e5734a";
  const border = verdict.tone === "buy" ? "#5fd98a" : "#e5734a";
  el.style.cssText = [
    "position:fixed",
    "pointer-events:auto",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif",
    "font-size:10px",
    "font-weight:700",
    "letter-spacing:0.02em",
    "padding:3px 6px",
    "border-radius:5px",
    "cursor:pointer",
    "line-height:1",
    "white-space:nowrap",
    `background:${bg}`,
    `color:${fg}`,
    `border:1px solid ${border}`,
    "box-shadow:0 2px 6px rgba(0,0,0,0.35)",
  ].join(";");
  el.addEventListener("click", (event) => {
    event.stopPropagation();
    event.preventDefault();
    onClick();
  });
  return el;
}

// Consecutive reposition ticks a badge's anchor may be missing/hidden
// before the badge is torn down entirely - a search results list is
// re-rendered often (scrolling, pagination, re-filtering), and removing
// on the very first missed tick would make badges flicker away and
// never come back once EA re-renders the same row. ~2s at the current
// interval is enough slack for a re-render without leaking badges
// forever once a row is genuinely gone (navigated away from Transfer
// Market, search cleared, etc).
const MAX_MISSING_TICKS = 8;

function positionBadge(entry) {
  const { el, anchorNode, priceText } = entry;
  // Re-verify the anchor still shows the price it matched, on EVERY
  // tick - not just when first found. EA's results list almost
  // certainly virtualizes/recycles row DOM nodes on scroll (a very
  // common pattern for long lists), which silently repoints an existing
  // text node at a DIFFERENT row's price without removing it from the
  // document. Without this check, a badge just keeps tracking that
  // node's new position forever - which is exactly what "floats around
  // the screen randomly" looks like: a BUY badge drifting to wherever
  // its originally-matched node happens to be reused next, no longer
  // anywhere near the row it was actually about.
  if (
    !anchorNode ||
    !anchorNode.parentElement ||
    !anchorNode.parentElement.isConnected ||
    anchorNode.nodeValue == null ||
    anchorNode.nodeValue.trim() !== priceText
  ) {
    el.style.display = "none";
    entry.missingTicks = (entry.missingTicks || 0) + 1;
    return;
  }
  const range = document.createRange();
  range.selectNodeContents(anchorNode);
  const rect = range.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) {
    el.style.display = "none";
    entry.missingTicks = (entry.missingTicks || 0) + 1;
    return;
  }
  entry.missingTicks = 0;
  el.style.display = "";
  // Sits just above-right of the matched price text, out of the way of
  // whatever EA renders beneath/around it.
  el.style.left = `${Math.max(4, rect.left)}px`;
  el.style.top = `${Math.max(4, rect.top - 20)}px`;
}

function repositionAll() {
  for (const [key, entry] of badges) {
    positionBadge(entry);
    if (entry.missingTicks >= MAX_MISSING_TICKS) {
      entry.el.remove();
      badges.delete(key);
    }
  }
}

function startRepositionLoop() {
  if (repositionTimer) return;
  repositionTimer = setInterval(repositionAll, REPOSITION_INTERVAL_MS);
}

function openOverlayFor(auction, sameCardAuctions, intelligence) {
  const combined = combineIntelligence(sameCardAuctions, intelligence);
  renderOverlay({
    kind: "result",
    key: `list-badge:${auction.sourceCardId}:${Date.now()}`,
    player: { name: intelligence.name, rarity: intelligence.rarity, rating: intelligence.rating },
    intelligence,
    combined,
  });
}

/**
 * Fetches intelligence for every distinct card among `auctions` (the
 * cache in api-client.js coalesces/reuses this even if called once per
 * detection event for an unchanged page) and badges each auction whose
 * verdict is BUY or AVOID. Safe to call repeatedly - existing badges for
 * unchanged auctions are left alone; stale ones (no longer matched in
 * the DOM) fade out via positionBadge()'s display:none path.
 */
export async function renderListBadges(auctions) {
  if (!Array.isArray(auctions) || auctions.length === 0) return { cardsChecked: 0, badgeCount: 0 };
  ensureContainer();
  startRepositionLoop();

  const byCard = new Map();
  for (const auction of auctions) {
    if (!byCard.has(auction.sourceCardId)) byCard.set(auction.sourceCardId, []);
    byCard.get(auction.sourceCardId).push(auction);
  }

  await Promise.all(
    [...byCard.keys()].map(async (cardId) => {
      const intelResult = await getCardIntelligence(cardId);
      if (intelResult.status !== "success") return; // no badge for not_found/errors - nothing confirmable
      const intelligence = intelResult.data;
      const sameCardAuctions = byCard.get(cardId);

      for (const auction of sameCardAuctions) {
        const key = auctionKey(auction);
        if (badges.has(key)) continue; // already badged this exact listing

        const verdict = verdictFor(auction, intelligence);
        if (!verdict) continue;

        const priceText = NUMBER_FORMAT.format(auction.buyNowPrice);
        const anchorNode = findPriceAnchor(priceText);
        if (!anchorNode) {
          log("list-badges: no DOM match for price", priceText, "- skipping this row's badge");
          continue;
        }

        const el = makeBadgeEl(verdict, () => openOverlayFor(auction, sameCardAuctions, intelligence));
        ensureContainer().appendChild(el);
        const entry = { el, auction, anchorNode, priceText, verdict };
        badges.set(key, entry);
        positionBadge(entry);
      }
    })
  );

  return { cardsChecked: byCard.size, badgeCount: badges.size };
}
