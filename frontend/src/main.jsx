import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";              // ✅ brings in Tailwind/global CSS
import { EntitlementsProvider } from "./context/EntitlementsContext";
import { queryClient } from "./lib/queryClient";
import { registerSW } from "./utils/pwa";
import ErrorBoundary from "./components/ErrorBoundary";

const container = document.getElementById("root");
if (!container) throw new Error('Root element "#root" not found');

// ErrorBoundary belongs here, outside every provider - App.jsx has its own
// nested one, but that only catches errors from inside <App>. If
// QueryClientProvider or EntitlementsProvider ever throw during the very
// first render (a missing/broken dependency, a provider bug), there was no
// boundary above them: React just unmounts to a blank white page with
// nothing but a console error to explain it. This one at least turns that
// into a visible "something went wrong" instead of silence.
createRoot(container).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <EntitlementsProvider>
          <App />
        </EntitlementsProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>
);

// PWA: the service worker + manifest were already in /public but never
// registered from the entry point - the install prompt and offline shell
// simply never activated.
registerSW();
