// The coin icon used to be an <img> pointing at cdn2.futbin.com - a
// third-party CDN belonging to the data source this product has migrated
// off. Every coin figure on every screen (there are hundreds) issued a
// request to it, so a block, a signed-URL expiry or a CDN change would
// have broken the icon site-wide, and every page view leaked traffic to
// a competitor's infrastructure. Inlined as an SVG: no request, no
// external dependency, and it renders identically offline.
function CoinIcon() {
  return (
    <svg className="coin-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="7" fill="#f2c94c" />
      <circle cx="8" cy="8" r="5" fill="none" stroke="#a97c1b" strokeWidth="1.2" />
      <path d="M8 4.6v6.8M6.2 6.2h3.6M6.2 9.8h3.6" stroke="#a97c1b" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

export function coinText(value, { signed = false, compact = false } = {}) {
  // `value == null` catches null AND undefined - deliberate loose equality.
  // Number(null) is 0 in JS (not NaN like Number(undefined)), so relying
  // on Number.isFinite alone let an explicit "no data" null through as a
  // real, displayable 0 - indistinguishable from a genuine zero value.
  if (value == null) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const absolute = Math.abs(number);
  const formatted = new Intl.NumberFormat("en-GB", compact && absolute >= 1000
    ? { notation: "compact", maximumFractionDigits: 1 }
    : { maximumFractionDigits: 0 }).format(absolute);
  if (!signed || number === 0) return formatted;
  return `${number > 0 ? "+" : "−"}${formatted}`;
}

export default function CoinValue({ value, signed = false, compact = false, className = "" }) {
  const text = coinText(value, { signed, compact });
  if (text === "—") return <span className={`coin-value coin-value-empty ${className}`.trim()}>—</span>;
  return (
    <span className={`coin-value ${className}`.trim()} aria-label={`${text} coins`}>
      <CoinIcon />
      <span>{text}</span>
    </span>
  );
}
