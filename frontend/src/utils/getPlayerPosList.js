// Always return a normalized array from any combination of fields.
import { normalizePositions } from "./positions";

export function getPlayerPosList(p) {
  // priority: explicit array → (position, altposition) → single position
  const raw = (Array.isArray(p?.positions) && p.positions.length)
    ? p.positions
    : [p?.position, p?.altposition].filter(Boolean);

  return normalizePositions(raw);
}