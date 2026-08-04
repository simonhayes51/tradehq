// src/v2/components/SectionCard.jsx
export default function SectionCard({ title, subtitle, children, className = "" }) {
  return (
    <div
      className={`rounded-[var(--v2-radius)] border border-[var(--v2-border)] bg-[var(--v2-card)] p-5 ${className}`}
    >
      {title && (
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-[var(--v2-text)]">{title}</h3>
          {subtitle && <p className="text-xs text-[var(--v2-muted)] mt-0.5">{subtitle}</p>}
        </div>
      )}
      {children}
    </div>
  );
}
