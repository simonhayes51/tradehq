// src/v2/pages/Chat/Chat.jsx
//
// The assistant. Replaced the v1 keyword-matcher "Trade Copilot", which
// has since been removed entirely.
//
// Every price/ROI/verdict the assistant states comes from a real tool
// call on the backend (see app/routers/v2/ai_chat.py) - and when a tool
// result carries numbers, this renders them as real components inline
// rather than trusting the model's prose to restate them correctly.
// Price age is always shown with a price, so a snapshot is never
// presented as if it were live.
import { useState } from "react";
import { Link } from "react-router-dom";
import { Send, Sparkles } from "lucide-react";
import { PageHead } from "../Players/Players";
import EmptyState from "../../components/EmptyState";
import PremiumGate from "../../components/PremiumGate";
import RecommendationBadge from "../../components/RecommendationBadge";
import CoinValue from "../../components/CoinValue";
import { useAiChat } from "../../hooks/useAiChat";
import "../../styles/v2-destinations.css";
import "./chat.css";

const SUGGESTIONS = ["Should I buy Mbappe 92?", "Who should I buy for 40k?", "How accurate are your recommendations?"];

// Only what the tools can actually answer. Anything outside this the
// assistant is instructed to decline rather than answer from the model's
// own (non-existent, and never current) market knowledge.
const CAPABILITIES = [
  "Look up any card's live price, verdict, buy/sell targets and risk",
  "Find trades that fit a coin budget right now",
  "Report how published recommendations have actually performed",
];

export default function Chat() {
  const { messages, sending, error, locked, unavailable, send } = useAiChat();
  const [draft, setDraft] = useState("");

  function submit(event) {
    event.preventDefault();
    const text = draft;
    setDraft("");
    send(text);
  }

  return (
    <main className="v2-destination">
      <PageHead
        eyebrow="AI TRADING ASSISTANT"
        title="Ask about any card or budget"
        copy="Every number in a reply comes from a live market lookup on this site - never invented, and never a prediction."
      />

      {unavailable ? (
        <EmptyState
          icon={<Sparkles />}
          title="The assistant isn't available right now"
          text="This part of EAFC.Live isn't switched on yet. Everything else - Opportunities, Trade Finder and player pages - works as normal."
        />
      ) : locked ? (
        <PremiumGate locked featureName="AI Chat" />
      ) : (
        <div className="v2-chat-panel">
          <div className="v2-chat-log">
            {messages.length === 0 ? (
              <EmptyState
                icon={<Sparkles />}
                title="Ask your first question"
                text="It answers from EAFC.Live's own market data only. It can:"
                action={
                  <>
                    <ul className="v2-chat-capabilities">
                      {CAPABILITIES.map((c) => <li key={c}>{c}</li>)}
                    </ul>
                    <div className="v2-chat-suggestions">
                      {SUGGESTIONS.map((s) => (
                        <button key={s} type="button" onClick={() => send(s)}>{s}</button>
                      ))}
                    </div>
                  </>
                }
              />
            ) : (
              messages.map((m, i) => <ChatBubble key={i} message={m} />)
            )}
            {sending ? <div className="v2-chat-bubble assistant pending">Thinking…</div> : null}
          </div>

          {error ? <p className="v2-chat-error">{error}</p> : null}

          <form className="v2-chat-input" onSubmit={submit}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Should I buy Mbappe 92?"
              disabled={sending}
            />
            <button type="submit" disabled={sending || !draft.trim()} aria-label="Send">
              <Send size={16} />
            </button>
          </form>
        </div>
      )}
    </main>
  );
}

function ChatBubble({ message }) {
  const isUser = message.role === "user";
  return (
    <div className={`v2-chat-bubble ${isUser ? "user" : "assistant"}`}>
      <p>{message.content}</p>
      {(message.toolResults || []).map((tr, i) => <ToolResult key={i} toolResult={tr} />)}
    </div>
  );
}

function ToolResult({ toolResult }) {
  const { tool, result } = toolResult;

  // These render the tool's OWN numbers as components, deliberately
  // alongside the model's prose rather than trusting the prose to restate
  // them correctly. Field names track app/routers/v2/ai_chat.py's
  // _chat_safe_card(), which is the only shape the model ever sees.
  if (tool === "resolve_and_evaluate_card" && result?.card) {
    return <ToolCard card={result.card} />;
  }

  if (tool === "find_trades_for_budget" && Array.isArray(result?.items) && result.items.length) {
    return (
      <div className="v2-chat-tool-list">
        {result.items.map((card) => <ToolCard key={card.card_id} card={card} compact />)}
      </div>
    );
  }

  if (tool === "get_track_record" && result?.has_enough_data) {
    return (
      <div className="v2-chat-tool-card">
        <div className="v2-chat-tool-head">
          <strong>Published recommendations, last {result.window_days} days</strong>
        </div>
        <span>
          {result.wins} won · {result.losses} lost · {result.win_rate_pct}% win rate · median{" "}
          {result.median_roi_pct}% after tax
        </span>
        <small>{result.caveat}</small>
      </div>
    );
  }

  if (result?.ambiguous && Array.isArray(result.candidates)) {
    return (
      <div className="v2-chat-tool-list">
        {result.candidates.map((c) => (
          <Link className="v2-chat-tool-row" key={c.card_id} to={`/v2/players/${c.card_id}`}>
            <span>{c.name} {c.rating}{c.version ? ` · ${String(c.version).replace(/_/g, " ")}` : ""}</span>
          </Link>
        ))}
      </div>
    );
  }

  return null;
}

// Price age is shown, always. A snapshot presented without its age is a
// claim that it is current, which is exactly the overstatement this
// assistant is not allowed to make.
function priceAgeLabel(minutes) {
  if (minutes == null) return null;
  const value = Number(minutes);
  if (!Number.isFinite(value)) return null;
  if (value < 60) return `${Math.round(value)} min old`;
  const hours = value / 60;
  if (hours < 24) return `${Math.round(hours)}h old`;
  return `${Math.round(hours / 24)}d old`;
}

function ToolCard({ card, compact = false }) {
  const age = priceAgeLabel(card.price_age_minutes);
  return (
    <Link className={`v2-chat-tool-card${compact ? " compact" : ""}`} to={`/v2/players/${card.card_id}`}>
      <div className="v2-chat-tool-head">
        <strong>{card.name} {card.rating}</strong>
        {card.verdict ? <RecommendationBadge recommendation={String(card.verdict).toLowerCase()} /> : null}
      </div>
      <div className="v2-chat-tool-numbers">
        {card.current_price != null ? <span>Now <CoinValue value={card.current_price} /></span> : null}
        {card.recommended_buy_max != null ? <span>Buy below <CoinValue value={card.recommended_buy_max} /></span> : null}
        {card.expected_profit_after_tax != null ? (
          <span>Profit <CoinValue value={card.expected_profit_after_tax} signed /> after tax</span>
        ) : null}
      </div>
      {age ? <small className="v2-chat-tool-age">Price {age}</small> : null}
    </Link>
  );
}
