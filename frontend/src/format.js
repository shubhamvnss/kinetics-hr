/**
 * Shared display formatting, so the KPI tiles and the Fix-with-AI screen present the same number
 * the same way.
 *
 * Two conventions from the pipeline that are easy to get backwards:
 *
 *  - variance_vs_target_pct is positive when a KPI is *worse* than target, whichever direction
 *    the KPI points (kpi_core.variance_pct). A bare "+26.7%" would read as "the value went up",
 *    so it is always rendered with the words "worse"/"better than target".
 *  - trend_direction means improving/worsening, not up/down in value
 *    (04_compute_kpis_to_postgres.py).
 */

export function formatValue(value, unit) {
  if (value === null || value === undefined) return "—";
  if (unit === "USD") return `$${Math.round(value).toLocaleString()}`;
  const rounded = Number.isInteger(value) ? value : Number(Number(value).toFixed(1));
  return rounded.toLocaleString();
}

export function unitSuffix(unit) {
  if (!unit || unit === "USD") return "";
  return unit === "%" ? "%" : ` ${unit}`;
}

export function varianceLabel(variance) {
  if (variance === null || variance === undefined) return { text: "—", tone: "neutral" };
  if (Math.abs(variance) < 0.05) return { text: "on target", tone: "neutral" };
  if (variance > 0) return { text: `${variance.toFixed(1)}% worse than target`, tone: "bad" };
  return { text: `${Math.abs(variance).toFixed(1)}% better than target`, tone: "good" };
}

export const TREND = {
  up: { glyph: "▲", tone: "good", label: "improving vs previous run" },
  down: { glyph: "▼", tone: "bad", label: "worsening vs previous run" },
  flat: { glyph: "—", tone: "neutral", label: "unchanged vs previous run" },
};

/** "7.8 days", "42%", "$13,240" - value and unit together. */
export function formatMetric(value, unit) {
  if (value === null || value === undefined) return "—";
  return `${formatValue(value, unit)}${unitSuffix(unit)}`;
}

export function formatTimestamp(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}
