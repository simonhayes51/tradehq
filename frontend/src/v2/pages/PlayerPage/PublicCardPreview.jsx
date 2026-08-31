// The card page as a signed-out visitor sees it.
//
// The server sends identity and an approximate price and nothing else
// (app/auth/access.py's public_card_preview), so there is no analysis in
// this component's props to hide - which is the point. This is not a
// blurred version of the real page; it is a different, smaller payload.
//
// It has one job beyond showing the card: make the free account worth
// creating by being specific about what it adds. A locked page that only
// says "sign up to see more" is asking someone to buy an unknown.
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Lock } from "lucide-react";
import PlayerCardArt from "../../../components/PlayerCardArt";
import { EVENTS, trackOnce } from "../../../lib/analytics";
import { FREE_LIMITS } from "../../lib/limits";
import { versionLabel } from "../../lib/format";
import "./public-card-preview.css";

// Concrete, and all of it true the moment the account exists.
const WHAT_AN_ACCOUNT_ADDS = [
  "The exact live price, and what the card is actually worth",
  "Recent completed sales and the full price history",
  "Whether it's a buy, a wait or one to avoid — and why",
  `${FREE_LIMITS.dailyOpportunities} complete trade ideas every day, with entry and sell prices`,
  `A watchlist of up to ${FREE_LIMITS.watchlist} cards and ${FREE_LIMITS.alerts} price alerts`,
];

export default function PublicCardPreview({ card, cardId, onBack, backLabel }) {
  const name = card.display_name || card.name || "This card";

  useEffect(() => {
    trackOnce(EVENTS.UPGRADE_PROMPT_VIEWED, { feature: "fair_value", surface: "player_page" }, "player_page");
  }, []);

  return (
    <main className="public-card-page">
      <button type="button" className="public-card-back" onClick={onBack}>{backLabel}</button>

      <section className="public-card-hero">
        <div className="public-card-art">
          <PlayerCardArt
            player={{
              name,
              rating: card.rating,
              version: card.rarity,
              position: card.primary_position || card.position,
              cardBgImage: card.card_design_image_url || null,
              cutoutImage: card.player_image_url || card.image_url || null,
              generatedCardUrl: card.composited_image_url || null,
            }}
            featured
          />
        </div>

        <div className="public-card-copy">
          <h1>{name}</h1>
          <div className="public-card-meta">
            {card.rating != null ? <span>{card.rating} OVR</span> : null}
            {card.primary_position || card.position ? <span>{card.primary_position || card.position}</span> : null}
            {card.rarity ? <span>{versionLabel(card.rarity)}</span> : null}
            {card.club ? <span>{card.club}</span> : null}
          </div>

          {/* The one number a signed-out visitor gets, and it is labelled
              as approximate every time it appears. It is here to prove
              the product is real and current, not to trade against. */}
          <dl className="public-card-price">
            <div>
              <dt>Around</dt>
              <dd>
                {card.current_bin_approx != null
                  ? `${Number(card.current_bin_approx).toLocaleString()} coins`
                  : "Price unavailable"}
              </dd>
            </div>
            <div className="locked">
              <dt>What it's worth</dt>
              <dd><Lock size={13} /> Free account</dd>
            </div>
            <div className="locked">
              <dt>Buy / sell / verdict</dt>
              <dd><Lock size={13} /> Free account</dd>
            </div>
          </dl>
          <p className="public-card-approx-note">
            Approximate. The exact live price is on the free account.
          </p>
        </div>
      </section>

      <section className="public-card-cta">
        <h2>See what this card is really worth</h2>
        <ul>
          {WHAT_AN_ACCOUNT_ADDS.map((line) => <li key={line}>{line}</li>)}
        </ul>
        <Link
          className="public-card-signup"
          to={`/signup?from=${encodeURIComponent(`/players/${cardId}`)}`}
        >
          Create free account <ArrowRight size={16} />
        </Link>
        <p className="public-card-reassure">
          Free permanently. No card required. You&apos;ll come straight back to this player.
        </p>
      </section>
    </main>
  );
}
