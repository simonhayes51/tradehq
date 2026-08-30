import { useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { Navigate, useSearchParams, Link } from 'react-router-dom';

const BENEFITS = [
  "Save cards to your Watchlist and track their price",
  "Log trades and see your real profit over time",
  "Unlock more EAFC Live features as you go",
];

// First-visitor framing: why bother creating an account, before the auth
// button. Discord is genuinely the only login method right now - this
// page says so plainly rather than dressing it up as a deliberate
// community-tie-in feature that doesn't actually exist yet.
const Signup = () => {
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
        <h1 className="text-3xl font-extrabold mb-6">Create your free account</h1>
        <ul className="text-left space-y-3 mb-8">
          {BENEFITS.map((b) => (
            <li key={b} className="flex items-start gap-2 text-sm text-gray-300">
              <span className="text-[#91db32] mt-0.5">✓</span>
              <span>{b}</span>
            </li>
          ))}
        </ul>
        <button
          onClick={() => login(from)}
          className="w-full bg-[#5865F2] hover:bg-[#4752c4] text-white font-bold py-3 px-6 rounded-lg transition-colors"
        >
          Continue with Discord
        </button>
        <p className="text-xs text-gray-500 mt-4">
          Discord is currently the only way to sign in - we use it only to create your
          EAFC Live account and identify your membership, never your EA login.
        </p>
        <p className="text-sm text-gray-400 mt-8">
          Already have an account? <Link to={`/login${from ? `?from=${encodeURIComponent(from)}` : ""}`} className="text-[#91db32] font-semibold hover:underline">Log in</Link>
        </p>
      </div>
    </div>
  );
};

export default Signup;
