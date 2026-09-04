import { useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { Navigate, useSearchParams, Link } from 'react-router-dom';
import './login.css';

// Return-visitor login. New-visitor framing (why bother creating an
// account) lives in Signup.jsx instead - two separate, honest intents
// rather than one page trying to be both. Both pages call the same
// Discord OAuth flow, so the copy here says so explicitly instead of
// letting "Continue with Discord" and "Create a free account" look like
// two different mechanisms.
const Login = () => {
  const { isAuthenticated, login, checkAuthStatus } = useAuth();
  const [searchParams] = useSearchParams();
  const from = searchParams.get('from');

  useEffect(() => {
    if (searchParams.get('authenticated') === 'true') {
      checkAuthStatus();
    }
  }, [searchParams, checkAuthStatus]);

  if (isAuthenticated) {
    return <Navigate to={from || "/"} replace />;
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <Link to="/" className="login-brand">
          <span className="brand-mark">EL</span>
          <span>EAFC <b>Live</b></span>
        </Link>
        <h1>Welcome back</h1>
        <p className="login-lede">Log in to see your watchlist, trades and alerts.</p>
        <button
          onClick={() => login(from)}
          className="login-cta"
        >
          Continue with Discord
        </button>
        <p className="login-explain">
          We'll create your free account if you're new. Discord is how EAFC Live signs
          you in and identifies your membership — we never see or store your EA login.
        </p>
        <p className="login-signup">
          New to EAFC Live? <Link to={`/signup${from ? `?from=${encodeURIComponent(from)}` : ""}`}>Create a free account</Link>
        </p>
        <p className="login-legal">
          By continuing you agree to our <Link to="/terms">Terms</Link> and{" "}
          <Link to="/privacy">Privacy Policy</Link>.
        </p>
      </div>
    </div>
  );
};

export default Login;
