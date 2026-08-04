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
      } else {
        setError(err.message || "Could not reach the AI chat.");
      }
    } finally {
      setSending(false);
    }
  }, [messages, sending]);

  return { messages, sending, error, locked, send };
}
