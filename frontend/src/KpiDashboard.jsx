/**
 * KpiDashboard - Corporate Loan (Deloitte Kinetic) KPI + root-cause dashboard.
 *
 * Renders two sections:
 *   1. Tier 1 KPI tiles  - one per headline KPI, value dominant, target and variance beneath.
 *   2. Root causes       - the ranked, evidence-backed causes for every Critical Tier 1 KPI.
 *
 * It is a pure presentation layer: everything it shows is read straight out of the pipeline's
 * JSON, and no number is recomputed here. The agent's contract (prompt section 23) is that
 * frontend_summary can be displayed as-is, so this component never re-ranks, re-aggregates or
 * re-words anything.
 *
 * Props
 *   tier1       - evidence.json -> tier1_all   (every Tier 1 KPI, for the tiles)
 *   analysis    - root_causes.json -> frontend_summary.analysis_summary
 *   runStats    - evidence.json -> run_stats   (optional, shown in the header)
 *   isSample    - true when bundled sample data is being displayed
 *   onFixWithAI - called as (kpi, cause) when a root cause's "Fix with AI" button is pressed.
 *                 App.jsx routes it to /root-cause/<RC-id>/fix-with-ai, where the id is
 *                 derived the same way the backend derives it (api/client.js rootCauseId).
 */
import { useMemo, useState } from "react";

import { TREND, formatValue, unitSuffix, varianceLabel } from "./format.js";

/* ------------------------------------------------------------------ formatting */

const HEALTH_ORDER = ["Critical", "Watch", "Healthy", "Informational", "N/A"];

/* ------------------------------------------------------------------ tiles */

function KpiTile({ kpi, rootCauseCount, onSelect }) {
  const variance = varianceLabel(kpi.variance_vs_target_pct);
  const trend = TREND[kpi.trend_direction];
  const health = kpi.health || "N/A";
  const isCritical = health === "Critical";
  const Tag = isCritical ? "button" : "div";

  return (
    <Tag
      className={`tile tile--${health.toLowerCase().replace("/", "")}`}
      {...(isCritical
        ? { type: "button", onClick: () => onSelect(kpi.kpi_id),
            "aria-label": `${kpi.kpi_name}: Critical. Jump to its root causes.` }
        : {})}
    >
      <header className="tile__head">
        <span className="tile__id">{kpi.kpi_id}</span>
        <span className={`badge badge--${health.toLowerCase().replace("/", "")}`}>{health}</span>
      </header>

      <h3 className="tile__title">{kpi.kpi_name}</h3>

      <p className="tile__value">
        {formatValue(kpi.current_value, kpi.unit)}
        <span className="tile__unit">{unitSuffix(kpi.unit)}</span>
        {trend && (
          <span className={`tile__trend tone--${trend.tone}`} title={trend.label}>
            {trend.glyph}
          </span>
        )}
      </p>

      <dl className="tile__meta">
        <div>
          <dt>Target</dt>
          <dd>
            {kpi.target === null || kpi.target === undefined
              ? "—"
              : `${formatValue(kpi.target, kpi.unit)}${unitSuffix(kpi.unit)}`}
          </dd>
        </div>
        <div>
          <dt>Variance</dt>
          <dd className={`tone--${variance.tone}`}>{variance.text}</dd>
        </div>
      </dl>

      {isCritical && rootCauseCount > 0 && (
        <p className="tile__link">
          {rootCauseCount} root {rootCauseCount === 1 ? "cause" : "causes"} →
        </p>
      )}
    </Tag>
  );
}

/* ------------------------------------------------------------------ root causes */

function RootCauseCard({ cause, kpi, runStats, onFix }) {
  const affected = cause.affected_cases || {};
  const totalCases = runStats?.cases_total;
  const disbursedCases = affected.count ?? runStats?.cases_disbursed;
  return (
    <article className="cause">
      <header className="cause__head">
        <span className="cause__rank">{cause.rank}</span>
        <h4 className="cause__title">{cause.root_cause}</h4>
        <span className={`pill pill--${(cause.confidence || "").toLowerCase()}`}>
          {cause.confidence} confidence
        </span>
      </header>

      {cause.root_cause_summary && <p className="cause__text">{cause.root_cause_summary}</p>}
      {cause.impact_summary && (
        <p className="cause__text cause__text--impact">
          <strong>Impact.</strong> {cause.impact_summary}
        </p>
      )}

      {!!(cause.statistics || []).length && (
        <ul className="stats">
          {cause.statistics.map((stat, i) => (
            <li className="stat" key={`${stat.metric}-${i}`}>
              <span className="stat__value">
                {formatValue(stat.value, stat.unit)}
                <span className="stat__unit">{unitSuffix(stat.unit)}</span>
              </span>
              <span className="stat__metric">{stat.metric}</span>
            </li>
          ))}
        </ul>
      )}

      <footer className="cause__foot">
        {disbursedCases !== null && disbursedCases !== undefined && (
          <span className="chip chip--cases">
            {totalCases != null
              ? `${totalCases.toLocaleString()} cases - ${disbursedCases.toLocaleString()} Disbursed`
              : `${disbursedCases.toLocaleString()} cases affected${
                  affected.percentage !== null && affected.percentage !== undefined
                    ? ` (${affected.percentage.toFixed(1)}%)`
                    : ""
                }`}
          </span>
        )}
        {(cause.linked_tier2_kpis || []).map((id) => (
          <span className="chip chip--kpi" key={id} title="Linked Tier 2 diagnostic KPI">
            {id}
          </span>
        ))}
        {(cause.gap_references || []).map((gap) => (
          <span className="chip chip--gap" key={gap} title="Documented process gap">
            {gap}
          </span>
        ))}
      </footer>

      <div className="cause__action">
        <FixWithAiButton kpi={kpi} cause={cause} onFix={onFix} />
      </div>
    </article>
  );
}

function FixWithAiButton({ kpi, cause, onFix }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleClick() {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      await onFix(kpi, cause);
    } catch (reason) {
      setError(reason?.message || "Unable to load Fix with AI.");
      setLoading(false);
    }
  }

  return (
    <div className="cause__action-group">
    <button
      type="button"
      className="btn-fix"
      onClick={handleClick}
      disabled={loading}
      aria-busy={loading}
      aria-label={`Fix with AI: generate recommendations for "${cause.root_cause}" on ${kpi.kpi_name}`}
    >
      {loading ? <span className="loading-spinner" aria-hidden="true" /> : <svg className="btn-fix__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M12 2.5l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1L5 9.5l5.1-1.9L12 2.5zM18.5 15l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9.9-2.4z"
          fill="currentColor"
        />
      </svg>}
      {loading ? "Loading recommendation..." : "Fix with AI"}
      {!loading && <span className="btn-fix__arrow" aria-hidden="true">→</span>}
    </button>
    {error && <span className="cause__action-error" role="alert">{error}</span>}
    </div>
  );
}

function CriticalKpiSection({ kpi, runStats, onFix }) {
  const variance = varianceLabel(kpi.variance_vs_target_pct);
  const causes = kpi.top_root_causes || [];
  return (
    <section className="rc" id={`rc-${kpi.kpi_id}`} tabIndex={-1}>
      <header className="rc__head">
        <div>
          <span className="rc__id">{kpi.kpi_id}</span>
          <h3 className="rc__title">{kpi.kpi_name}</h3>
        </div>
        <p className="rc__numbers">
          <strong>
            {formatValue(kpi.current_value, kpi.unit)}
            {unitSuffix(kpi.unit)}
          </strong>
          <span>
            vs target {formatValue(kpi.target, kpi.unit)}
            {unitSuffix(kpi.unit)}
          </span>
          <span className={`tone--${variance.tone}`}>{variance.text}</span>
        </p>
      </header>

      {kpi.kpi_performance_summary && <p className="rc__summary">{kpi.kpi_performance_summary}</p>}
      {kpi.why_not_performing && (
        <p className="rc__why">
          <strong>Why it is not performing.</strong> {kpi.why_not_performing}
        </p>
      )}

      {causes.length ? (
        <div className="rc__causes">
          {causes.map((cause) => (
            <RootCauseCard
              cause={cause}
              kpi={kpi}
              runStats={runStats}
              onFix={onFix}
              key={`${kpi.kpi_id}-${cause.rank}`}
            />
          ))}
        </div>
      ) : (
        <p className="empty">No evidence-backed root cause was returned for this KPI.</p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ dashboard */

export default function KpiDashboard({
  tier1 = [],
  analysis = null,
  runStats = null,
  isSample = false,
  onFixWithAI = null,
}) {
  const [filter, setFilter] = useState("All");

  const handleFix = (kpi, cause) => onFixWithAI?.(kpi, cause);

  const criticalKpis = analysis?.critical_kpis || [];
  const causeCountById = useMemo(
    () => Object.fromEntries(criticalKpis.map((k) => [k.kpi_id, (k.top_root_causes || []).length])),
    [criticalKpis]
  );

  // Fall back to the KPIs named in the analysis when no evidence package is present, so the
  // tiles still render from a single file.
  const tiles = tier1.length ? tier1 : criticalKpis;

  const counts = useMemo(() => {
    const seed = Object.fromEntries(HEALTH_ORDER.map((h) => [h, 0]));
    tiles.forEach((k) => {
      const h = k.health || "N/A";
      seed[h] = (seed[h] || 0) + 1;
    });
    return seed;
  }, [tiles]);

  const visible = filter === "All" ? tiles : tiles.filter((k) => (k.health || "N/A") === filter);

  const jumpTo = (kpiId) => {
    const el = document.getElementById(`rc-${kpiId}`);
    if (!el) return;
    // Focus first: calling focus() after scrollIntoView cancels the smooth-scroll animation.
    el.focus({ preventScroll: true });
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  };

  return (
    <div className="dash">
      {isSample && (
        <p className="banner banner--sample" role="status">
          <strong>Sample data.</strong> These figures are bundled placeholders, not your KPI run.
          Write the real files to <code>public/data/</code> to replace them — see the frontend README.
        </p>
      )}

      <header className="dash__head">
        <div>
          <h1>Corporate Loan — KPI Health &amp; Root Causes</h1>
        </div>
        {runStats && (
          <div className="runstats-wrap">
            <div className="runstats-tiles">
              <article className="runstats-tile">
                <h3>Cases</h3>
                <p className="runstats-tile__value">
                  {(runStats.cases_total ?? 0).toLocaleString()}
                </p>
              </article>
              <article className="runstats-tile">
                <h3>Disbursed</h3>
                <p className="runstats-tile__value">
                  {(runStats.cases_disbursed ?? 0).toLocaleString()}
                </p>
              </article>
            </div>
          </div>
        )}
      </header>

      <section className="section">
        <div className="section__head">
          <h2>Tier 1 KPIs</h2>
          <div className="filters" role="group" aria-label="Filter KPIs by health">
            {["All", ...HEALTH_ORDER.filter((h) => counts[h] > 0)].map((option) => (
              <button
                key={option}
                type="button"
                className={`filter ${filter === option ? "filter--on" : ""}`}
                onClick={() => setFilter(option)}
              >
                {option}
                <span className="filter__count">
                  {option === "All" ? tiles.length : counts[option]}
                </span>
              </button>
            ))}
          </div>
        </div>

        {visible.length ? (
          <div className="tiles">
            {visible.map((kpi) => (
              <KpiTile
                key={kpi.kpi_id}
                kpi={kpi}
                rootCauseCount={causeCountById[kpi.kpi_id] || 0}
                onSelect={jumpTo}
              />
            ))}
          </div>
        ) : (
          <p className="empty">No Tier 1 KPI data loaded.</p>
        )}
      </section>

      <section className="section">
        <div className="section__head">
          <h2>Root causes — Critical KPIs</h2>
          {!!criticalKpis.length && (
            <span className="section__note">
              {criticalKpis.length} Critical Tier 1 {criticalKpis.length === 1 ? "KPI" : "KPIs"},
              ranked by evidence and business impact
            </span>
          )}
        </div>

        {analysis?.overall_observation && (
          <p className="observation">
            <strong>Overall.</strong> {analysis.overall_observation}
          </p>
        )}

        {criticalKpis.length ? (
          criticalKpis.map((kpi) => (
            <CriticalKpiSection kpi={kpi} runStats={runStats} key={kpi.kpi_id} onFix={handleFix} />
          ))
        ) : (
          <p className="empty">
            No root-cause analysis loaded. Run{" "}
            <code>python root_cause_agent.py --out frontend/public/data/root_causes.json</code> and
            reload.
          </p>
        )}
      </section>
    </div>
  );
}
