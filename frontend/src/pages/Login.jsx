import { useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { Navigate, useSearchParams, Link } from 'react-router-dom';

// Return-visitor login. New-visitor framing (why bother creating an
// account) lives in Signup.jsx instead - two separate, honest intents
// rather than one page trying to be both.
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
    <div className="min-h-screen bg-black text-white flex items-center justify-center px-4">
      <div className="w-full max-w-sm text-center">
        <Link to="/" className="inline-block mb-8 text-sm font-bold text-[#91db32]">EAFC Live</Link>
        <h1 className="text-3xl font-extrabold mb-2">Welcome back</h1>
        <p className="text-gray-400 mb-8 text-sm">Log in to see your watchlist, trades and alerts.</p>
        <button
          onClick={() => login(from)}
          className="w-full bg-[#5865F2] hover:bg-[#4752c4] text-white font-bold py-3 px-6 rounded-lg transition-colors"
        >
          Continue with Discord
        </button>
        <p className="text-xs text-gray-500 mt-4">
          We only use Discord to create your EAFC Live account and identify your membership.
        </p>
        <p className="text-sm text-gray-400 mt-8">
          New to EAFC Live? <Link to={`/signup${from ? `?from=${encodeURIComponent(from)}` : ""}`} className="text-[#91db32] font-semibold hover:underline">Create a free account</Link>
        </p>
      </div>
    </div>
  );
};

export default Login;
