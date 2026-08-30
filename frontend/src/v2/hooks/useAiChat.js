// src/v2/hooks/useAiChat.js
//
// Local conversation state, not a react-query cache - a chat thread
// isn't a resource to refetch/invalidate, it's an append-only exchange
// the user is actively driving. locked tracks the backend's 402 (not
// Pro) distinctly from a transient error, matching RecommendationSection's
// existing 401/402-is-locked convention.
import { useCallback, useState } from "react";
import { sendChatMessage } from "../api/aiChat";

export function useAiChat() {
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState(false);
  // 503 = the assistant isn't configured/reachable on this deployment.
  // Distinct from `error` (one message failed) and `locked` (not on a
  // paid plan): there is nothing to retry, so the input is hidden rather
  // than inviting the user to try again forever.
  const [unavailable, setUnavailable] = useState(false);

  const send = useCallback(async (text) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;

    setError("");
    const next = [...messages, { role: "user", content: trimmed }];
    setMessages(next);
    setSending(true);

    try {
      const result = await sendChatMessage(next);
      setMessages((current) => [
        ...current,
        { role: "assistant", content: result.reply || "", toolResults: result.toolResults || [] },
      ]);
    } catch (err) {
      if (err.status === 401 || err.status === 402) {
        setLocked(true);
      } else if (err.status === 503) {
        setUnavailable(true);
      } else if (err.status === 429) {
        setError("You're sending messages a bit fast - give it a moment and try again.");
      } else {
        setError(err.message || "Could not reach the assistant. Check your connection and try again.");
      }
      // The user's own message stays in the thread (it's what they typed),
      // but nothing is appended as an assistant reply - an error must
      // never be rendered as though the assistant said it.
    } finally {
      setSending(false);
    }
  }, [messages, sending]);

  return { messages, sending, error, locked, unavailable, send };
}
