import type { ReactNode } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  ChevronDown,
  Database,
  RefreshCw,
} from "lucide-react";
import type { Provenance } from "./api";

export const number = (value: number | null | undefined, digits = 0) =>
  value == null
    ? "—"
    : value.toLocaleString("en-GB", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      });
export const gw = (value: number | null | undefined) =>
  value == null ? "—" : number(value / 1000, 2);
export const signed = (value: number | null | undefined) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${number(value)}`;
export const clock = (value: string | number) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
export const dayLabel = (value: string) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value.length === 10 ? `${value}T12:00:00Z` : value));
export const stamp = (value: string | null) =>
  value ? `${dayLabel(value)}, ${clock(value)} GMT` : "Unavailable";

export function Metric({
  label,
  value,
  unit,
  note,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  unit?: string;
  note: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <article className={`metric ${tone}`}>
      <div className="metric-label" title={hint}>
        {label}
        {hint && <span className="info">i</span>}
      </div>
      <div className="metric-value">
        {value}
        <span>{unit}</span>
      </div>
      <div className="metric-note">{note}</div>
    </article>
  );
}
export function Panel({
  title,
  subtitle,
  aside,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function ApiState({
  error,
  retry,
}: {
  error: string | null;
  retry: () => void;
}) {
  return error ? (
    <div className="error-state" role="alert">
      <AlertCircle />
      <h2>We couldn’t load this analysis</h2>
      <p>{error}</p>
      <button className="button" onClick={retry}>
        <RefreshCw size={15} />
        Try again
      </button>
    </div>
  ) : (
    <div className="loading-state" role="status">
      <span className="spinner" />
      <p>Reconstructing the decision-time view…</p>
      <div className="skeleton-grid">
        {[0, 1, 2, 3].map((n) => (
          <div key={n} />
        ))}
      </div>
    </div>
  );
}
export function ProvenanceDetails({ data }: { data: Provenance }) {
  return (
    <details className="provenance">
      <summary>
        <span>
          <Database size={15} /> Analysis provenance & methodology
        </span>
        <ChevronDown size={15} />
      </summary>
      <div className="provenance-content">
        <dl>
          {Object.entries({
            Population: data.population,
            Actuals: data.source_actuals,
            Forecasts: data.source_forecasts,
            "Decision scope": data.decision_scope,
            Selection: data.selection,
            "Error convention": data.error_convention,
            Resolution: data.units,
            Uncertainty: data.uncertainty,
            "Source retrieved": stamp(data.source_retrieved_at),
          }).map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mono">
          Snapshot SHA-256: {data.source_hash || "Not recorded"}
        </p>
        <div className="source-links">
          <a
            href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream"
            target="_blank"
            rel="noreferrer"
          >
            FUELHH documentation <ArrowUpRight size={13} />
          </a>
          <a
            href="https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream"
            target="_blank"
            rel="noreferrer"
          >
            WINDFOR documentation <ArrowUpRight size={13} />
          </a>
          <a
            href="https://github.com/adityaapraveen/gdscokbirl/blob/main/docs/RESEARCH.md"
            target="_blank"
            rel="noreferrer"
          >
            Research & limitations <ArrowUpRight size={13} />
          </a>
        </div>
      </div>
    </details>
  );
}
