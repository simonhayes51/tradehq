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
    // Preserve where the user was headed (e.g. a shared /players/:id
    // link) so login() can send them back there instead of stranding
    // them on Home after auth - see AuthContext.login's returnTo.
    const current = window.location.hash.slice(1) || '/';
    window.location.hash = `/login?from=${encodeURIComponent(current)}`;
    return null;
  }

  return children;
};

export default PrivateRoute;
