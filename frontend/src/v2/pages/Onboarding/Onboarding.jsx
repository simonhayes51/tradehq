// src/v2/pages/Onboarding/Onboarding.jsx
//
// Where a new member lands after signup, instead of on an unexplained
// dashboard.
//
// Two questions, both skippable, and both are read by the product: the
// coin budget seeds Trade Finder and the affordability hint, and the
// alerts answer decides whether we offer the alert prompt after a card is
// watched. Nothing else is asked - a question whose answer changes
// nothing is worse than no question, because it implies a
// personalisation that does not exist.
//
// Then it shows the member their actual free picks and explains the
// numbers on them, because "here is what you came for" is a better first
// screen than a tour.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Bell, Check, Loader2, Star, Wallet } from "lucide-react";
import { useEntitlements } from "../../../context/EntitlementsContext";
import { EVENTS, track, trackOnce } from "../../../lib/analytics";
import { useFutggOpportunities } from "../../hooks/useFutggMarket";
import { useCompleteOnboarding, useOnboarding } from "../../hooks/useOnboarding";
import { addWatch } from "../../../api/watchlist";
import CoinValue from "../../components/CoinValue";
import EmptyState from "../../components/EmptyState";
import { formatCountdown } from "../../lib/format";
import "./onboarding.css";

const BUDGETS = [
  { label: "Under 25k", value: 25_000 },
  { label: "25k – 150k", value: 150_000 },
  { label: "150k – 1m", value: 1_000_000 },
  { label: "Over 1m", value: 5_000_000 },
];

export default function Onboarding() {
  const navigate = useNavigate();
  const { tier } = useEntitlements();
  const { data: state, isLoading } = useOnboarding();
  const complete = useCompleteOnboarding();
  const [step, setStep] = useState(0);
  const [budget, setBudget] = useState(null);
  const [wantsAlerts, setWantsAlerts] = useState(null);
  const [watched, setWatched] = useState({});

  const oppsQuery = useFutggOpportunities({ page_size: 12 });
  // The free picks are the ones the server actually unlocked. Anything
  // still locked is not shown here - a welcome screen is the worst place
  // to introduce a paywall.
  const picks = useMemo(
    () => (oppsQuery.data?.items ?? []).filter((item) => !item.locked).slice(0, 2),
    [oppsQuery.data],
  );
  const meta = oppsQuery.data?.free_picks;

  useEffect(() => { trackOnce(EVENTS.ONBOARDING_STARTED, { tier }); }, [tier]);

  // A member who has already been through this does not see it again,
  // even by typing the URL.
  useEffect(() => {
    if (!isLoading && state && state.needsOnboarding === false && step === 0) {
      navigate("/", { replace: true });
    }
  }, [isLoading, state, step, navigate]);

  function finish({ skipped }) {
    track(skipped ? EVENTS.ONBOARDING_SKIPPED : EVENTS.ONBOARDING_COMPLETED, { tier, step });
    complete.mutate(
      { coin_budget: budget, wants_price_alerts: wantsAlerts, skipped },
      { onSettled: () => navigate("/", { replace: true }) },
    );
  }

  async function watch(item) {
    const id = String(item.source_card_id);
    if (watched[id]) return;
    setWatched((w) => ({ ...w, [id]: "saving" }));
    try {
      await addWatch({
        player_name: item.display_name || item.name,
        card_id: id, version: item.rarity ?? null, platform: "ps", source: "futgg",
      });
      setWatched((w) => ({ ...w, [id]: "saved" }));
      track(EVENTS.FIRST_WATCHLIST_CARD_ADDED, { surface: "onboarding" });
    } catch {
      setWatched((w) => ({ ...w, [id]: "error" }));
    }
  }

  return (
    <main className="onboarding">
      <header className="onboarding-head">
        <span className="onboarding-brand">EAFC <b>Live</b></span>
        <button type="button" className="onboarding-skip" onClick={() => finish({ skipped: true })}>
          Skip
        </button>
      </header>

      {step === 0 ? (
        <section className="onboarding-step">
          <h1>Welcome. Two quick questions.</h1>
          <p className="onboarding-lede">
            Both are optional and you can change them later. We only ask things that change
            what you see.
          </p>

          <fieldset className="onboarding-field">
            <legend><Wallet size={15} /> Roughly what can you spend on a card?</legend>
            <p className="onboarding-hint">Used to pre-fill your budget and flag what you can afford.</p>
            <div className="onboarding-options">
              {BUDGETS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={budget === option.value ? "selected" : ""}
                  aria-pressed={budget === option.value}
                  onClick={() => setBudget(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="onboarding-field">
            <legend><Bell size={15} /> Want price alerts on cards you track?</legend>
            <p className="onboarding-hint">We'll offer to set one when you add a card to your watchlist.</p>
            <div className="onboarding-options">
              <button type="button" className={wantsAlerts === true ? "selected" : ""}
                      aria-pressed={wantsAlerts === true} onClick={() => setWantsAlerts(true)}>
                Yes, offer alerts
              </button>
              <button type="button" className={wantsAlerts === false ? "selected" : ""}
                      aria-pressed={wantsAlerts === false} onClick={() => setWantsAlerts(false)}>
                No thanks
              </button>
            </div>
          </fieldset>

          <button className="onboarding-next" onClick={() => setStep(1)}>
            Show me today's picks <ArrowRight size={16} />
          </button>
        </section>
      ) : (
        <section className="onboarding-step">
          <h1>Your free picks for today</h1>
          <p className="onboarding-lede">
            You get {meta?.allowance ?? 2} complete trade ideas every day. Here's how to read one.
          </p>

          {oppsQuery.isLoading ? (
            <EmptyState text="Loading today's picks…" />
          ) : picks.length ? (
            <div className="onboarding-picks">
              {picks.map((item) => (
                <article className="onboarding-pick" key={item.source_card_id}>
                  <div className="onboarding-pick-head">
                    <strong>{item.display_name || item.name}</strong>
                    <span>{item.rating} · {String(item.rarity || "Card").replace(/_/g, " ")}</span>
                  </div>
                  <dl className="onboarding-pick-numbers">
                    <div>
                      <dt>Buy below</dt>
                      <dd><CoinValue value={item.recommended_buy_max} /></dd>
                      <small>Pay more than this and the trade stops making sense.</small>
                    </div>
                    <div>
                      <dt>Sell target</dt>
                      <dd><CoinValue value={item.recommended_sell_target} /></dd>
                      <small>List around here — it's where the card has been selling.</small>
                    </div>
                    <div>
                      <dt>Expected profit</dt>
                      <dd className="positive"><CoinValue value={item.expected_profit_after_tax} signed /></dd>
                      <small>After EA's 5% sale tax, already deducted.</small>
                    </div>
                    <div>
                      <dt>Risk</dt>
                      <dd>{String(item.risk_level || "unrated").replace(/^\w/, (c) => c.toUpperCase())}</dd>
                      <small>How reliably this card has been selling.</small>
                    </div>
                  </dl>
                  <button className="onboarding-watch" onClick={() => watch(item)}
                          disabled={watched[String(item.source_card_id)] === "saving"}>
                    <Star size={15} fill={watched[String(item.source_card_id)] === "saved" ? "currentColor" : "none"} />
                    {watched[String(item.source_card_id)] === "saved" ? "On your watchlist" : "Watch this card"}
                  </button>
                </article>
              ))}
            </div>
          ) : (
            // Honest, not apologetic: some days genuinely have nothing
            // that clears the bar for a free pick.
            <EmptyState
              text="No pick has cleared our bar yet today. We'd rather show you nothing than something we don't believe in — check back after the next refresh."
            />
          )}

          {meta?.nextRefreshAt ? (
            <p className="onboarding-refresh">
              Your next free picks arrive in <b>{formatCountdown(meta.nextRefreshAt)}</b>, at 6pm UK time.
            </p>
          ) : null}

          <div className="onboarding-finish">
            <button className="onboarding-next" onClick={() => finish({ skipped: false })}
                    disabled={complete.isPending}>
              {complete.isPending ? <><Loader2 size={16} className="spin" /> Setting up…</> : <><Check size={16} /> Start trading</>}
            </button>
            {/* Present, not pushed. An upgrade offer on the welcome screen
                should be a link, not an interruption. */}
            <Link className="onboarding-upgrade" to="/billing">
              Want the full feed and Trade Finder? See Elite
            </Link>
          </div>
        </section>
      )}
    </main>
  );
}
