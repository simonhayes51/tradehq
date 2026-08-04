export const COIN_ICON_URL = "https://cdn2.futbin.com/https%3A%2F%2Fcdn.futbin.com%2Fdesign%2Fimg%2Fcoins_big.png?fm=png&ixlib=java-2.1.0&w=20&s=723885ca3b3ab1cf3cb11c53f9408968";

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
      <img src={COIN_ICON_URL} alt="" aria-hidden="true" />
      <span>{text}</span>
    </span>
  );
}
