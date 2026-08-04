import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Send, Bot, User, Sparkles, TrendingUp, AlertCircle, Loader } from 'lucide-react';
import api from '../axios';
import toast from 'react-hot-toast';

const TradeCopilot = () => {
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: "👋 Hey! I'm your AI Trading Copilot. I can help you with:\n\n• Market analysis and trends\n• Player price predictions\n• Trading strategies\n• Risk assessment\n• Portfolio optimization\n\nWhat would you like to know?",
      timestamp: new Date()
    }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const quickPrompts = [
    { icon: '📈', text: 'What players should I invest in right now?' },
    { icon: '💰', text: 'How can I make 100k coins this week?' },
    { icon: '⚡', text: 'Best flipping strategies for beginners?' },
    { icon: '🎯', text: 'Analyze my recent trading performance' }
  ];

  const sendMessage = async (messageText = input) => {
    if (!messageText.trim() || loading) return;

    const userMessage = {
      role: 'user',
      content: messageText,
      timestamp: new Date()
    };

    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const { data } = await api.post('/api/ai/copilot', {
        messages: [...messages, userMessage].map(m => ({
          role: m.role,
          content: m.content
        })),
        context: {
          includePortfolio: true,
          includeMarketData: true
        }
      });

      const assistantMessage = {
        role: 'assistant',
        content: data.response,
        suggestions: data.suggestions,
        timestamp: new Date()
      };

      setMessages(prev => [...prev, assistantMessage]);
    } catch (error) {
      toast.error(error.userMessage || 'Failed to get response');

      // Add error message
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: "Sorry, I'm having trouble connecting right now. Please try again in a moment.",
        isError: true,
        timestamp: new Date()
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="min-h-screen bg-[#0e1320] text-white flex flex-col">
      {/* Header */}
      <div className="border-b border-white/10 bg-slate-900/50 backdrop-blur-lg sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-purple-600 to-purple-400 rounded-xl flex items-center justify-center">
              <Sparkles className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-black">AI Trade Copilot</h1>
              <p className="text-xs text-slate-400">Powered by advanced trading AI</p>
            </div>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-5xl mx-auto space-y-4">
          <AnimatePresence>
            {messages.map((message, idx) => (
              <motion.div
                key={idx}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                {message.role === 'assistant' && (
                  <div className="w-8 h-8 bg-gradient-to-br from-purple-600 to-purple-400 rounded-lg flex items-center justify-center flex-shrink-0">
                    <Bot className="w-5 h-5" />
                  </div>
                )}

                <div className={`max-w-[80%] ${message.role === 'user' ? 'order-1' : ''}`}>
                  <div
                    className={`rounded-2xl px-4 py-3 ${
                      message.role === 'user'
                        ? 'bg-gradient-to-br from-purple-600 to-purple-500 text-white'
                        : message.isError
                        ? 'bg-red-900/30 border border-red-500/30'
                        : 'bg-slate-800/50 border border-white/10'
                    }`}
                  >
                    <p className="whitespace-pre-wrap text-sm leading-relaxed">
                      {message.content}
                    </p>
                  </div>

                  {/* Suggestions */}
                  {message.suggestions && message.suggestions.length > 0 && (
                    <div className="mt-2 space-y-2">
                      <p className="text-xs text-slate-400 px-2">Suggested actions:</p>
                      {message.suggestions.map((suggestion, sIdx) => (
                        <button
                          key={sIdx}
                          onClick={() => sendMessage(suggestion.action)}
                          className="block w-full text-left px-4 py-2 bg-slate-800/30 hover:bg-slate-700/50
                                   rounded-lg text-sm border border-white/5 transition-all"
                        >
                          <span className="font-medium text-purple-400">{suggestion.title}</span>
                          <p className="text-xs text-slate-400 mt-1">{suggestion.description}</p>
                        </button>
                      ))}
                    </div>
                  )}

                  <p className="text-xs text-slate-500 mt-1 px-2">
                    {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </p>
                </div>

                {message.role === 'user' && (
                  <div className="w-8 h-8 bg-gradient-to-br from-slate-700 to-slate-600 rounded-lg flex items-center justify-center flex-shrink-0">
                    <User className="w-5 h-5" />
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>

          {loading && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex gap-3"
            >
              <div className="w-8 h-8 bg-gradient-to-br from-purple-600 to-purple-400 rounded-lg flex items-center justify-center">
                <Bot className="w-5 h-5" />
              </div>
              <div className="bg-slate-800/50 border border-white/10 rounded-2xl px-4 py-3">
                <div className="flex items-center gap-2">
                  <Loader className="w-4 h-4 animate-spin text-purple-400" />
                  <span className="text-sm text-slate-400">Thinking...</span>
                </div>
              </div>
            </motion.div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Quick Prompts */}
      {messages.length === 1 && (
        <div className="px-6 pb-4">
          <div className="max-w-5xl mx-auto">
            <p className="text-xs text-slate-400 mb-2">Quick questions:</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {quickPrompts.map((prompt, idx) => (
                <button
                  key={idx}
                  onClick={() => sendMessage(prompt.text)}
                  className="text-left px-4 py-3 bg-slate-800/30 hover:bg-slate-700/50 rounded-xl
                           border border-white/5 transition-all text-sm"
                >
                  <span className="mr-2">{prompt.icon}</span>
                  {prompt.text}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Input */}
      <div className="border-t border-white/10 bg-slate-900/50 backdrop-blur-lg sticky bottom-0">
        <div className="max-w-5xl mx-auto px-6 py-4">
          <div className="flex gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ask me anything about FUT trading..."
              disabled={loading}
              className="flex-1 bg-slate-800/50 border border-white/10 rounded-xl px-4 py-3
                       focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 outline-none
                       disabled:opacity-50 disabled:cursor-not-allowed"
            />
            <button
              onClick={() => sendMessage()}
              disabled={!input.trim() || loading}
              className="bg-gradient-to-r from-purple-600 to-purple-500 hover:from-purple-500 hover:to-purple-400
                       disabled:from-slate-700 disabled:to-slate-600 disabled:cursor-not-allowed
                       rounded-xl px-6 py-3 font-bold transition-all shadow-lg"
            >
              <Send className="w-5 h-5" />
            </button>
          </div>
          <p className="text-xs text-slate-500 mt-2">
            💡 Tip: Ask specific questions for better advice. Include player names, budgets, or timeframes.
          </p>
        </div>
      </div>
    </div>
  );
};

export default TradeCopilot;
