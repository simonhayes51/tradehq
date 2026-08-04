import { useAuth } from '../context/AuthContext';
import Loading from './Loading';

const PrivateRoute = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();

  // Don't protect if we're on access-denied
  if (window.location.pathname === '/access-denied' || 
      window.location.pathname.includes('access-denied')) {
    return children;
  }

  if (isLoading) {
    return <Loading />;
  }

  if (!isAuthenticated) {
    // Use hash routing for login
    window.location.hash = '/login';
    return null;
  }

  return children;
};

export default PrivateRoute;
