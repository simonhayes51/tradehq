// src/v2/pages/Chat/Chat.jsx
//
// Built fresh in the v2 design system, not a port of the old (v1,
// keyword-matcher-backed) src/pages/TradeCopilot.jsx. Every price/ROI/
// recommendation the assistant states comes from a real tool call on the
// backend (see app/routers/v2/ai_chat.py) - when a tool result carries a
// recommendation verdict, this renders the real RecommendationBadge +
// CoinValue components inline instead of trusting the model's prose to
// restate the numbers correctly.
import { useState } from "react";
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

export default function Chat() {
  const { messages, sending, error, locked, send } = useAiChat();
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
        copy="Every number in a reply comes from a real recommendation, trade-finder, or track-record lookup - never invented."
      />

      {locked ? (
        <PremiumGate locked featureName="AI Chat" />
      ) : (
        <div className="v2-chat-panel">
          <div className="v2-chat-log">
            {messages.length === 0 ? (
              <EmptyState
                icon={<Sparkles />}
                title="Ask your first question"
                text="Try one of these, or ask your own."
                action={
                  <div className="v2-chat-suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button key={s} type="button" onClick={() => send(s)}>{s}</button>
                    ))}
                  </div>
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

  if (tool === "resolve_and_evaluate_card" && result?.recommendation && !result.recommendation.error) {
    const rec = result.recommendation;
    return (
      <div className="v2-chat-tool-card">
        <div className="v2-chat-tool-head">
          <strong>{result.card?.name} {result.card?.rating}</strong>
          <RecommendationBadge recommendation={String(rec.status || "").toLowerCase()} />
        </div>
        {rec.entry_price != null ? <span>Buy below <CoinValue value={rec.entry_price} /></span> : null}
      </div>
    );
  }

  if (tool === "find_trades_for_budget" && Array.isArray(result?.items) && result.items.length) {
    return (
      <div className="v2-chat-tool-list">
        {result.items.slice(0, 5).map((item) => (
          <div className="v2-chat-tool-row" key={item.card_id}>
            <span>{item.name} {item.rating}</span>
            <CoinValue value={item.current_price} />
            <CoinValue value={item.est_profit_after_tax} signed />
          </div>
        ))}
      </div>
    );
  }

  if (tool === "get_track_record" && Array.isArray(result?.strategies)) {
    return (
      <div className="v2-chat-tool-list">
        {result.strategies.filter((s) => s.hasEnoughData).map((s) => (
          <div className="v2-chat-tool-row" key={s.strategy}>
            <span>{s.label}</span>
            <span>{s.hitRatePct}% hit rate</span>
          </div>
        ))}
      </div>
    );
  }

  if (result?.ambiguous && Array.isArray(result.candidates)) {
    return (
      <div className="v2-chat-tool-list">
        {result.candidates.map((c) => (
          <div className="v2-chat-tool-row" key={c.card_id}>
            <span>{c.name} {c.rating} · {c.version}</span>
          </div>
        ))}
      </div>
    );
  }

  return null;
}
