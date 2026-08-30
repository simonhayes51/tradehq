// src/components/ErrorBoundary.jsx
import React from 'react';

/**
 * Catches render errors below it.
 *
 * Two behaviours that were missing and both showed up in testing:
 *
 * 1. It never reset. Once anything threw, EVERY subsequent route showed
 *    "Something went wrong" - navigating away, using the back button, or
 *    clicking any link left the same dead screen, because `hasError`
 *    stayed true for the life of the mount. One broken page took the
 *    whole app down until the user thought to reload. `resetKey` (the
 *    current location, supplied by App.jsx) clears the error when the
 *    user navigates, so a bad page is a bad page and not a bad session.
 *
 * 2. The only way out was "Refresh Page". A full reload is the right
 *    escape hatch when the error is on the page you're on, but "go
 *    somewhere that works" is usually what the user wants, so both are
 *    offered.
 *
 * The reload button deliberately uses window.location rather than the
 * router: if the router itself is what threw, a router-based escape
 * would throw again.
 */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  static getDerivedStateFromProps(props, state) {
    // Navigating counts as "try something else" - drop the error so the
    // new route gets a chance to render.
    if (state.hasError && props.resetKey !== state.resetKey) {
      return { hasError: false, error: null, resetKey: props.resetKey };
    }
    if (props.resetKey !== state.resetKey) {
      return { resetKey: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error, errorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-black text-white flex items-center justify-center">
          <div className="text-center px-6">
            <h1 className="text-2xl font-bold mb-3">This page didn&apos;t load</h1>
            <p className="text-gray-400 mb-6">
              Something went wrong displaying it. Your account and data are unaffected.
            </p>
            <div className="flex flex-wrap gap-3 justify-center">
              <button
                onClick={() => window.location.reload()}
                className="bg-white/10 hover:bg-white/20 border border-white/20 px-4 py-2 rounded"
              >
                Try again
              </button>
              <a
                href="#/"
                onClick={() => this.setState({ hasError: false, error: null })}
                className="bg-[#39ff8a] hover:brightness-110 text-black font-bold px-4 py-2 rounded"
              >
                Back to Home
              </a>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
