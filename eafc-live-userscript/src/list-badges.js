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
// WHY BADGES ARE NOW INSERTED INTO THE ROW, NOT POSITION:FIXED-TRACKED
// -------------------------------------------------------------------
// v1 placed a `position: fixed` badge at the matched price node's
// on-screen coordinates, recomputed on a 250ms timer, appended to this
// module's OWN container - never inserted into EA's row itself. Live
// tested on a real multi-card Grid Mode search: badges were absent
// entirely from the visible rows (only the unrelated single-card
// overlay showed anything), and the explicit ask was to stop "hovering"
// and be embedded in the row instead.
//
// Badges are now inserted as an actual DOM sibling right after the
// matched price element - `insertAdjacentElement("afterend", ...)` - so
// normal document flow positions and scrolls it with zero coordinate
// tracking or per-frame math. This can't drift, can't end up floating
// disconnected from its row, and needs no reposition loop at all. The
// non-interference guarantee still holds the same way: nothing is
// removed or altered on EA's own row, only a new small element is added
// next to it - a wrong or fragile price match still just means "no
// badge for that row," never a change to EA's own layout or click
// handling. verifyBadge() (replacing the old positionBadge()) still
// runs periodically, but only to detect a recycled/stale row (list
// virtualization reusing the same DOM node for a different card - see
// its own comment) and remove the badge then; it no longer computes or
// writes any position.
import { getCardIntelligence } from "./api-client.js";
import { combineIntelligence } from "./market-calc.js";
import { render as renderOverlay } from "./overlay.js";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
const VERIFY_INTERVAL_MS = 500;

// auctionKey -> { el, auction, anchorNode, priceText, verdict, missingTicks }
const badges = new Map();
let verifyTimer = null;

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC Live]", ...args);
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
 * Badges never need excluding from this search themselves - their
 * label text ("✓ BUY" / "⚠ AVOID") can never equal a formatted price.
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
  while ((node = walker.nextNode())) candidates.push(node);
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
  // No position/top/left - this is a genuine sibling of the row's price
  // element now (see this file's header), positioned by normal document
  // flow like any other inline element EA itself rendered there.
  el.style.cssText = [
    "display:inline-flex",
    "align-items:center",
    "pointer-events:auto",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif",
    "font-size:10px",
    "font-weight:700",
    "letter-spacing:0.02em",
    "padding:3px 6px",
    "margin:4px 0 0 6px",
    "border-radius:5px",
    "cursor:pointer",
    "line-height:1",
    "white-space:nowrap",
    "vertical-align:middle",
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

// Consecutive verify ticks a badge's anchor may fail to match before the
// badge is torn down entirely - a search results list is re-rendered
// often (scrolling, pagination, re-filtering), and removing on the very
// first missed tick would make badges flicker away and never come back
// once EA re-renders the same row. ~4s at the current interval is enough
// slack for a re-render without leaking badges forever once a row is
// genuinely gone (navigated away from Transfer Market, search cleared).
const MAX_MISSING_TICKS = 8;

/**
 * No position math anymore - the badge lives in the row's own DOM flow
 * (see this file's header), so it moves/scrolls with it for free. This
 * only ever needs to catch ONE thing: EA's results list almost
 * certainly virtualizes/recycles row DOM nodes on scroll (a very common
 * pattern for long lists), which can silently repoint the anchor text
 * node at a DIFFERENT row's price without removing it from the
 * document - if that happens, this badge is now sitting in a row about
 * a different card than the one it claims, which has to be torn down
 * rather than left showing a stale verdict.
 */
function verifyBadge(entry) {
  const { anchorNode, priceText } = entry;
  const stillValid =
    anchorNode &&
    anchorNode.parentElement &&
    anchorNode.parentElement.isConnected &&
    anchorNode.nodeValue != null &&
    anchorNode.nodeValue.trim() === priceText;
  entry.missingTicks = stillValid ? 0 : (entry.missingTicks || 0) + 1;
}

function verifyAll() {
  for (const [key, entry] of badges) {
    verifyBadge(entry);
    if (entry.missingTicks >= MAX_MISSING_TICKS) {
      entry.el.remove();
      badges.delete(key);
    }
  }
}

function startVerifyLoop() {
  if (verifyTimer) return;
  verifyTimer = setInterval(verifyAll, VERIFY_INTERVAL_MS);
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
 * unchanged auctions are left alone; stale ones (row recycled for a
 * different card - see verifyBadge()) are removed from the DOM entirely.
 */
export async function renderListBadges(auctions) {
  if (!Array.isArray(auctions) || auctions.length === 0) return { cardsChecked: 0, badgeCount: 0 };
  startVerifyLoop();

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

        // Inserted right after the price element itself, as a genuine
        // sibling in the row's own DOM - see this file's header for why,
        // in place of the old position:fixed + coordinate-tracking
        // approach. anchorNode is the Text node showing the price;
        // insertAdjacentElement needs an Element, hence parentElement.
        const el = makeBadgeEl(verdict, () => openOverlayFor(auction, sameCardAuctions, intelligence));
        anchorNode.parentElement.insertAdjacentElement("afterend", el);
        const entry = { el, auction, anchorNode, priceText, verdict, missingTicks: 0 };
        badges.set(key, entry);
      }
    })
  );

  return { cardsChecked: byCard.size, badgeCount: badges.size };
}
