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
// WHY BADGES SIT INSIDE THE ROW CARD, ABSOLUTELY POSITIONED - NOT
// POSITION:FIXED-TRACKED, AND NOT A PLAIN INLINE SIBLING EITHER
// -------------------------------------------------------------------
// v1 placed a `position: fixed` badge at the matched price node's
// on-screen coordinates, recomputed on a 250ms timer, appended to this
// module's OWN container - never inserted into EA's row itself. Live
// tested on a real multi-card Grid Mode search: badges were absent
// entirely from the visible rows, and the explicit ask was to stop
// "hovering" and be embedded in the row instead.
// v2 tried inserting the badge as a genuine inline DOM sibling right
// after the matched price element. Live tested immediately after: still
// invisible, on rows that WERE flagged (the summary overlay's count
// went up). The likely cause is the mirror image of the nav-tab saga's
// squeeze bug (see nav-tab.js's header) - a row card built for a fixed
// set of children (image, name, price, time) with a fixed height and/or
// overflow:hidden can silently clip or collapse an unplanned EXTRA
// child, exactly the way appending an unplanned 7th nav tab silently
// shrank six others instead of erroring.
//
// Badges now go into the row CARD (found by findRowContainer() below,
// climbing from the price up to the nearest ancestor whose own text
// contains both "Buy Now" and "Start Price" - a decent proxy for "the
// whole card, not just the price's immediate wrapper") as `position:
// absolute`, anchored to that card's own top-right corner. Taking it
// out of flow entirely means it can't be squeezed or clipped by
// whatever fixed layout the card uses for ITS OWN children, the same
// reasoning that fixed the nav badge - while still being a child of the
// row card, not the viewport, so it moves and scrolls with the row for
// free and needs no coordinate-tracking loop. The non-interference
// guarantee still holds: nothing on the card is removed, resized, or
// altered - only a new small absolutely-positioned element is added,
// which cannot itself disturb the card's own layout. verifyBadge()
// still runs periodically, but only to detect a recycled/stale row
// (list virtualization reusing the same DOM node for a different card)
// and remove the badge then.
import { getCardIntelligence } from "./api-client.js";
import { combineIntelligence } from "./market-calc.js";
import { render as renderOverlay } from "./overlay.js";

const NUMBER_FORMAT = new Intl.NumberFormat("en-US");
const VERIFY_INTERVAL_MS = 500;

// auctionKey -> { el, auction, anchorNode, priceText, verdict, missingTicks }
const badges = new Map();
let verifyTimer = null;
let lastRowDiagnostic = "no badge inserted yet";

function log(...args) {
  if (typeof window !== "undefined" && window.EAFC_LIVE_DEBUG) console.log("[EAFC.Live]", ...args);
}

/** Short diagnostic string describing the last row container a badge
 * was actually inserted into - see the assignment site's comment. */
export function getListBadgesDiagnostic() {
  return lastRowDiagnostic;
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

// A single row card in every screenshot seen so far tops out somewhere
// around 150-200px tall (image + name + price/bid + time). Comfortably
// generous ceiling for "still plausibly one row" before assuming an
// ancestor has crossed into a container holding multiple rows.
const MAX_PLAUSIBLE_ROW_HEIGHT_PX = 320;

/**
 * Climbs from the matched price node up to the LARGEST ancestor still
 * under MAX_PLAUSIBLE_ROW_HEIGHT_PX tall - a proxy for "the whole row
 * card," not just the small element directly wrapping the price.
 *
 * Replaced a text-content heuristic (climb until an ancestor's text
 * contains both "Buy Now" and "Start Price") that live-tested as still
 * invisible even though the flagged count went up - most likely because
 * that condition can be satisfied by a container holding SEVERAL rows,
 * not just one (every row repeats both labels), landing the badge at
 * the corner of a shared wrapper instead of an individual card, quite
 * possibly off-screen or overlapping unrelated content. Actual rendered
 * geometry is a more reliable signal than hoping text boundaries line
 * up with visual ones - a genuine row card is comfortably below this
 * height ceiling, while a multi-row wrapper is not.
 */
function findRowContainer(anchorNode) {
  let el = anchorNode.parentElement;
  let best = el;
  for (let depth = 0; el && depth < 14; depth++, el = el.parentElement) {
    const rect = el.getBoundingClientRect();
    if (rect.height <= 0) continue;
    if (rect.height > MAX_PLAUSIBLE_ROW_HEIGHT_PX) break;
    best = el;
  }
  return best;
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
  // position:absolute, anchored to the row CARD's own top-right corner
  // (see this file's header for why this replaced plain in-flow
  // insertion) - out of the row's own layout flow entirely, so it can't
  // be squeezed or clipped by whatever fixed structure the card uses
  // for its own children, while still being a child of the card (not
  // the viewport), so it scrolls and moves with it for free.
  el.style.cssText = [
    "position:absolute",
    "top:6px",
    "right:6px",
    "z-index:2147483645",
    "display:inline-flex",
    "align-items:center",
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

// Consecutive verify ticks a badge's anchor may fail to match before the
// badge is torn down entirely - a search results list is re-rendered
// often (scrolling, pagination, re-filtering), and removing on the very
// first missed tick would make badges flicker away and never come back
// once EA re-renders the same row. ~4s at the current interval is enough
// slack for a re-render without leaking badges forever once a row is
// genuinely gone (navigated away from Transfer Market, search cleared).
const MAX_MISSING_TICKS = 8;

/**
 * Live test: badges appeared correctly but "disappear very quickly" -
 * much faster than this loop's own MAX_MISSING_TICKS countdown could
 * explain on its own. Most likely cause: a countdown label in the same
 * row ("Time: <15 Seconds") ticks down often enough that EA's own
 * rendering REPLACES the row's DOM node outright on each tick rather
 * than mutating it in place - which destroys this badge (a child of the
 * now-discarded old node) instantly, and since nothing but a genuine
 * market re-fetch calls renderListBadges() again, a badge lost this way
 * would otherwise stay gone until the next real detection event, not
 * just for the few missing ticks it takes to notice.
 *
 * Self-heals instead: when the anchor no longer matches, this tries
 * findPriceAnchor() again right away and re-parents the SAME badge
 * element into whatever row shows that price now, rather than only
 * counting down toward removal. A badge should come back within one
 * verify tick of a re-render, not stay gone until the whole page's
 * auctions are re-fetched. Only counts toward MAX_MISSING_TICKS (and
 * eventual removal) when even a fresh search finds nothing - the row is
 * genuinely gone, not just re-rendered.
 */
function verifyBadge(entry) {
  const { anchorNode, priceText } = entry;
  const stillValid =
    anchorNode &&
    anchorNode.parentElement &&
    anchorNode.parentElement.isConnected &&
    anchorNode.nodeValue != null &&
    anchorNode.nodeValue.trim() === priceText;
  if (stillValid) {
    entry.missingTicks = 0;
    return;
  }
  const freshAnchor = findPriceAnchor(priceText);
  if (freshAnchor) {
    const row = findRowContainer(freshAnchor);
    if (getComputedStyle(row).position === "static") row.style.position = "relative";
    row.appendChild(entry.el);
    entry.anchorNode = freshAnchor;
    entry.missingTicks = 0;
    return;
  }
  entry.missingTicks = (entry.missingTicks || 0) + 1;
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

        // Appended into the row CARD (not just the price's immediate
        // wrapper) as position:absolute - see this file's header for the
        // two prior attempts this replaced and why. The card needs a
        // positioned ancestor for that to anchor against; only touches
        // `position` when it's still the browser default (`static`), so
        // it can't alter how the card's own children are laid out.
        const row = findRowContainer(anchorNode);
        if (getComputedStyle(row).position === "static") row.style.position = "relative";
        const el = makeBadgeEl(verdict, () => openOverlayFor(auction, sameCardAuctions, intelligence));
        row.appendChild(el);
        const entry = { el, auction, anchorNode, priceText, verdict, missingTicks: 0 };
        badges.set(key, entry);
        // Two prior rounds both looked right in code but weren't visible
        // live - this records concrete geometry from the LAST insertion
        // (surfaced in main.js's "watching" overlay text) so the next
        // round has a real number to react to instead of another guess.
        const rowRect = row.getBoundingClientRect();
        lastRowDiagnostic = `row: ${Math.round(rowRect.width)}x${Math.round(rowRect.height)}px @(${Math.round(rowRect.left)},${Math.round(rowRect.top)})`;
      }
    })
  );

  return { cardsChecked: byCard.size, badgeCount: badges.size };
}
