import { Navigate, useLocation } from "react-router-dom";

export default function Trades() {
  const location = useLocation();
  return <Navigate to={`/v2/club${location.search || ""}`} replace />;
}
