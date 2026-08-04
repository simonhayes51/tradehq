import { useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { Navigate, useSearchParams } from 'react-router-dom';

const Login = () => {
  const { isAuthenticated, login, checkAuthStatus } = useAuth();
  const [searchParams] = useSearchParams();

  // Check if user was redirected back from OAuth
  useEffect(() => {
    if (searchParams.get('authenticated') === 'true') {
      checkAuthStatus();
    }
  }, [searchParams, checkAuthStatus]);

  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-4xl font-bold mb-8">FUT Trading Dashboard</h1>
        <p className="text-gray-400 mb-8">
          Track your FIFA Ultimate Team trades and profits
        </p>
        <button
          onClick={login}
          className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded-lg transition-colors"
        >
          Login with Discord
        </button>
      </div>
    </div>
  );
};

export default Login;
