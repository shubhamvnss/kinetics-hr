import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import React from "react";
import { formatValue, unitSuffix, varianceLabel } from "../format.js";
import { createDeploymentPackage, getFixWithAI, getToBeTransformation } from "../api/client.js";
import "../fixwithai.css";

const TO_BE_CACHE_VERSION = 2;
const TO_BE_MAX_POLL_DURATION_MS = 5 * 60 * 1000;

const array = (value) =>
  Array.isArray(value)
    ? value
    : value && typeof value === "object"
    ? Object.values(value)
    : value == null
    ? []
    : [value];

const text = (value) =>
  value && typeof value === "object"
    ? JSON.stringify(value)
    : String(value ?? "—");

const label = (value) =>
  String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

function EvidenceChain({ items }) {
  const values = array(items);
  if (!values.length) return null;

  return (
    <details className="evidence-disclosure">
      <summary>
        Evidence chain from the root-cause analysis
        <span>{values.length} signals</span>
      </summary>
      <ol className="evidence-chain__list">
        {values.map((item, index) => (
          <li className="evidence-chain__item" key={index}>
            <b>{String(index + 1).padStart(2, "0")}</b>
            <div>
              <strong>
                {item?.metric || item?.label || `Evidence signal ${index + 1}`}
              </strong>
              <p>{item?.evidence_text || item?.detail || text(item)}</p>
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}

function AgentCard({ agent, index }) {
  return (
    <article className="ai-agent-card">
      <header>
        <b>{String(index + 1).padStart(2, "0")}</b>
        <div>
          <h3>{agent?.name || `AI agent ${index + 1}`}</h3>
          <small>{agent?.agent_id || "governed-agent"}</small>
        </div>
      </header>
      <p>{agent?.purpose}</p>
      <div className="ai-agent-card__grid">
        <div>
          <strong>Trigger</strong>
          <span>
            {array(agent?.trigger).join(" · ") || "Process exception detected"}
          </span>
        </div>
        <div>
          <strong>Actions</strong>
          <span>
            {array(agent?.action).join(" · ") || "Prepare recommendation"}
          </span>
        </div>
        <div>
          <strong>Systems</strong>
          <span>
            {array(agent?.systems).join(" · ") || "Kinetic process data"}
          </span>
        </div>
        <div>
          <strong>Expected outcome</strong>
          <span>{agent?.expected_outcome || "Auditable handling"}</span>
        </div>
      </div>
      {agent?.human_in_loop && <aside>{agent.human_in_loop_detail}</aside>}
    </article>
  );
}

export default function FixWithAIPage() {
  const { rootCauseId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const { kpi, cause } = location.state || {};
  const [recommendation, setRecommendation] = React.useState(
    location.state?.recommendation || null
  );
  const [error, setError] = React.useState(null);
  const [agentLoading, setAgentLoading] = React.useState(false);
  const [toBeDesign, setToBeDesign] = React.useState(null);
  const [toBeError, setToBeError] = React.useState(null);
  const [toBeLoading, setToBeLoading] = React.useState(false);

  React.useEffect(() => {
    if (recommendation) return undefined;

    const controller = new AbortController();

    getFixWithAI(rootCauseId, { signal: controller.signal })
      .then(setRecommendation)
      .catch((reason) => {
        if (reason.name !== "AbortError") setError(reason);
      });

    return () => controller.abort();
  }, [rootCauseId, recommendation]);

  React.useEffect(() => {
    if (!rootCauseId) return undefined;

    const controller = new AbortController();
    let cancelled = false;
    const startedAt = Date.now();
    let timeoutId;

    setToBeDesign(null);
    setToBeLoading(true);
    setToBeError(null);

    const poll = async () => {
      if (cancelled) return;

      try {
        const result = await getToBeTransformation(rootCauseId, {
          signal: controller.signal,
        });

        if (result && result.status === "running") {
          const retrySeconds =
            typeof result.retry_after_seconds === "number"
              ? result.retry_after_seconds
              : 3;

          if (Date.now() - startedAt >= TO_BE_MAX_POLL_DURATION_MS) {
            setToBeLoading(false);
            setToBeError(
              new Error(
                "To-be transformation is taking longer than expected. Try again later.",
              ),
            );
            return;
          }

          timeoutId = window.setTimeout(poll, retrySeconds * 1000);
          return;
        }

        setToBeDesign(result);
        setToBeLoading(false);
      } catch (reason) {
        if (reason.name === "AbortError") return;
        setToBeError(reason);
        setToBeLoading(false);
      }
    };

    poll();

    return () => {
      cancelled = true;
      controller.abort();
      if (timeoutId && typeof window !== "undefined") {
        window.clearTimeout(timeoutId);
      }
    };
  }, [rootCauseId]);

  if (!kpi || !cause)
    return (
      <main className="dash fix-page">
        <section className="fix-error">
          <h1>Root cause context is unavailable</h1>
          <Link className="btn-secondary" to="/">
            Back to root causes
          </Link>
        </section>
      </main>
    );

  const root = recommendation?.root_cause || cause;
  const impact = recommendation?.impact || {};
  const variance = varianceLabel(
    root.kpi_variance_vs_target_pct ?? kpi.variance_vs_target_pct
  );
  const metrics = array(impact.metrics);
  const hasToBeDesign = !!toBeDesign;
  const toBe = toBeDesign || {};
  const comparison = toBe.current_vs_to_be?.comparison || null;
  const currentState = array(toBe.current_state || toBe.current_state_activities);
  const toBeState = array(toBe.to_be_state || toBe.to_be_activities);
  const futureSteps = hasToBeDesign
    ? toBeState.map((activity, index) => {
      const rawType =
        activity.activity_type ||
        activity.change_type ||
        activity.responsible_agent ||
        "";
      const value = String(rawType).toLowerCase();
      let type = "existing";
      if (value.includes("ai")) type = "ai_agent";
      else if (value.includes("human")) type = "human";
      else if (value.includes("auto")) type = "automated";

      return {
        id:
          activity.to_be_step_id ||
          activity.to_be_sequence ||
          activity.sequence ||
          index,
        name:
          activity.to_be_activity ||
          activity.activity_name ||
          activity.current_activity,
        description:
          activity.expected_outcome ||
          activity.reason_for_change ||
          "",
        type,
      };
    })
    : array(recommendation?.future_state?.steps).filter((step) => {
      const name = String(step?.name || "").toLowerCase();
      const description = String(step?.description || "").toLowerCase();
      if (!name && !description) return true;
      if (name.includes("current process baseline")) return false;
      if (
        description.includes(
          "current as-is process for the diagnosed root cause",
        )
      )
        return false;
      return true;
    });
  const agenticWorkflow = array(toBe.agentic_workflow);
  const traceabilityRows = array(toBe.traceability);
  const summary = toBe.summary || {};
  const kpiContext = toBe.kpi_context || {};
  const tier1Kpi = kpiContext.tier_1_kpi || null;
  const tier2Kpis = array(kpiContext.tier_2_kpis);
  const gaps = array(kpiContext.gaps);
  const rootCauseContext = kpiContext.root_cause || null;

  const activitiesCurrent =
    summary.current_activity_count ??
    toBe.current_state_metrics?.activity_count ??
    comparison?.number_of_activities?.current ??
    null;
  const activitiesToBe =
    summary.to_be_activity_count ??
    toBe.to_be_metrics?.to_be_activity_count ??
    comparison?.number_of_activities?.to_be ??
    null;
  const stepsEliminated =
    summary.reduced_steps ??
    toBe.current_vs_to_be?.reduced_steps ??
    null;
  const aiActivities =
    comparison?.ai_agent_activities?.to_be ??
    toBe.to_be_metrics?.ai_agent_activity_count ??
    null;
  const deterministicActivities =
    comparison?.deterministic_automation_activities?.to_be ??
    toBe.to_be_metrics?.deterministic_activity_count ??
    null;
  const humanActivities =
    comparison?.human_approval_activities?.to_be ??
    toBe.to_be_metrics?.human_activity_count ??
    null;

  const toBeSummaryCards = [
    {
      key: "activities",
      label: "Activities",
      value:
        activitiesCurrent != null && activitiesToBe != null
          ? `${activitiesCurrent} -> ${activitiesToBe}`
          : activitiesToBe ?? activitiesCurrent,
      sub:
        activitiesCurrent != null &&
        activitiesToBe != null &&
        activitiesToBe < activitiesCurrent
          ? `${activitiesCurrent - activitiesToBe} fewer steps`
          : null,
    },
    {
      key: "steps_eliminated",
      label: "Steps eliminated",
      value: stepsEliminated,
    },
    {
      key: "ai_agent_activities",
      label: "AI agent activities",
      value: aiActivities,
    },
    {
      key: "deterministic_activities",
      label: "Deterministic automation",
      value: deterministicActivities,
    },
    {
      key: "human_activities",
      label: "Human activities",
      value: humanActivities,
    },
  ].filter((card) => card.value != null);

  const mixLabel = (key) => {
    switch (key) {
      case "ai_agent":
      case "ai":
        return "AI agent";
      case "automated":
      case "deterministic":
        return "Deterministic automation";
      case "human":
        return "Human";
      case "existing":
      default:
        return "Existing step";
    }
  };

  const eliminatedActivities = array(
    toBe.current_vs_to_be?.activities_eliminated ||
      toBe.current_vs_to_be?.eliminated_activities ||
      [],
  );

  const getSystems = (activity) =>
    array(activity.system_used || activity.systems || activity.system || []);

  const systemNames = [
    ...new Set(
      [
        ...array(toBe.current_vs_to_be?.key_systems),
        ...array(kpiContext.key_systems),
        ...toBeState.flatMap(getSystems),
      ]
        .map(String)
        .filter(Boolean),
    ),
  ];

  const impactRationale = array(
    toBe.current_vs_to_be?.impact_and_rationale ||
      kpiContext.impact_and_rationale ||
      [],
  );

  const mixCounts = {
    ai: futureSteps.filter((step) => step.type === "ai_agent").length,
    deterministic: futureSteps.filter((step) => step.type === "automated").length,
    human: futureSteps.filter((step) => step.type === "human").length,
    existing: futureSteps.filter((step) => step.type === "existing").length,
  };
  const changeMixTotal =
    Object.values(mixCounts).reduce((total, count) => total + count, 0) || null;

  const hasSystems = toBeState.some(
    (activity) => getSystems(activity).length > 0
  );
  const diagramSrc =
    toBe.diagram_png_base64
      ? `data:image/png;base64,${toBe.diagram_png_base64}`
      : null;

  const repCase = recommendation?.representative_case || null;
  const cycleTime = repCase?.cycle_time ?? repCase?.total_cycle_time_days ?? null;
  const slaDays = repCase?.sla ?? root.kpi_target ?? null;
  const slaVariance =
    repCase?.sla_breach ??
    (cycleTime != null && slaDays != null ? Number((cycleTime - slaDays).toFixed(1)) : null);
  const reworkLoops = repCase?.rework_loops ?? null;
  const handoffs = repCase?.handoffs ?? null;
  const manualTouches = repCase?.manual_touches ?? null;
  const keyEvents = array(repCase?.key_events);

  async function fixWithAgent() {
    setAgentLoading(true);
    setError(null);

    try {
      const agents = array(recommendation?.recommendation_plan?.agents);
      const systems = [
        ...new Set(
          agents.flatMap((agent) => array(agent?.systems).map(String))
        ),
      ];

      const packageResult = await createDeploymentPackage({
        kpi,
        root_cause: root,
        evidence: root.evidence || cause.statistics || [],
        recommendation: {
          ...recommendation,
          agents,
          recommendation_plan: {
            ...(recommendation?.recommendation_plan || {}),
            agents,
          },
        },
        process_flow: {
          systems,
          future_state: recommendation?.future_state || {},
        },
        context: {
          impacted_cases: recommendation?.representative_case
            ? [recommendation.representative_case]
            : [],
          event_log: recommendation?.representative_case?.key_events || [],
          recommendation_plan: { agents },
          tier2_kpis: array(root.tier_2_kpi || cause.linked_tier2_kpis),
          gaps: root.gap_references || cause.gap_references || [],
        },
      });

      navigate(`/root-cause/${rootCauseId}/deployment-package`, {
        state: { kpi, cause, packageResult },
      });
    } catch (reason) {
      setError(reason);
      setAgentLoading(false);
    }
  }

  return (
    <main className="dash fix-page">
      <nav className="fix-breadcrumb">
        <Link to="/">KPI Health &amp; Root Causes</Link>
        <span>›</span>
        <span>Fix with AI</span>
      </nav>

      <header className="fix-head ai-fix-header">
        <div>
          <h1>AI Fix Recommendation</h1>
          <p className="dash__sub">{root.root_cause_id || rootCauseId}</p>
        </div>
      </header>

      <section className="fix-section opportunity-card">
        <header className="fix-section__head">
          <div>
            <span className="package-section__eyebrow">AI Fix Opportunity</span>
            <h2>{root.title || cause.root_cause}</h2>
          </div>
        </header>

        <p className="fix-rc__desc">
          {root.description || cause.root_cause_summary}
        </p>

        <div className="fix-kpi-context">
          <span className="chip chip--t1">
            {root.tier_1_kpi_id || kpi.kpi_id} {root.tier_1_kpi || kpi.kpi_name}
          </span>
          {array(root.tier_2_kpi || cause.linked_tier2_kpis).map((id) => (
            <span className="chip chip--t2" key={id}>
              {id}
            </span>
          ))}
        </div>

        <p className="fix-kpi-line">
          <strong>
            {formatValue(
              root.kpi_current_value ?? kpi.current_value,
              root.kpi_unit || kpi.unit
            )}
            {unitSuffix(root.kpi_unit || kpi.unit)}
          </strong>
          {" "}
          against a target of
          {" "}
          {formatValue(
            root.kpi_target ?? kpi.target,
            root.kpi_unit || kpi.unit
          )}
          {unitSuffix(root.kpi_unit || kpi.unit)} —
          {" "}
          <span className={`tone--${variance.tone}`}>{variance.text}</span>
        </p>

        <div className="impact-grid">
          <div className="impact-total">
            <strong>{impact.impacted_cases ?? "—"}</strong>
            <span>Cases impacted</span>
            <small>{impact.impact_percentage ?? "—"}% of total cases</small>
          </div>

          {metrics.map((metric, index) => (
            <article
              className="impact-card"
              key={metric.label || index}
            >
              <strong>
                {metric.value ?? "—"} {metric.unit || ""}
              </strong>
              <span>{metric.label}</span>
              <small>{metric.detail}</small>
            </article>
          ))}
        </div>

        <EvidenceChain items={root.evidence || cause.statistics} />
      </section>

      <section className="fix-section">
        <header className="fix-section__head">
          <h2>Most Representative Case</h2>
        </header>

        {recommendation?.representative_case ? (
          <div className="fix-case">
            <div className="fix-case__head">
              <div>
                <h3>{recommendation.representative_case.case_id}</h3>
                <p className="fix-case__sub">
                  {root.tier_1_kpi || kpi.kpi_name}  b7 {root.tier_1_kpi_id || kpi.kpi_id}
                </p>
              </div>
              <dl className="fix-case__stats">
                <div>
                  <dt>Cycle time</dt>
                  <dd>{cycleTime != null ? `${cycleTime} days` : "-"}</dd>
                </div>
                <div>
                  <dt>SLA</dt>
                  <dd>{slaDays != null ? `${slaDays} days` : "-"}</dd>
                </div>
                <div>
                  <dt>SLA variance</dt>
                  <dd>{slaVariance != null ? `${slaVariance} days` : "-"}</dd>
                </div>
                <div>
                  <dt>Rework loops</dt>
                  <dd>{reworkLoops != null ? reworkLoops : "-"}</dd>
                </div>
                <div>
                  <dt>Handoffs</dt>
                  <dd>{handoffs != null ? handoffs : "-"}</dd>
                </div>
                <div>
                  <dt>Manual touches</dt>
                  <dd>{manualTouches != null ? manualTouches : "-"}</dd>
                </div>
              </dl>
            </div>

            <h4 className="fix-case__h4">What happened</h4>
            <ol className="fix-timeline">
              {array(recommendation.representative_case.key_events).map((event) => (
                <li
                  key={event.sequence}
                  className={`fix-tl ${
                    event.matched_root_cause ? "fix-tl--matched" : ""
                  }`}
                >
                  <span className="fix-tl__dot" />
                  <p className="fix-tl__activity">
                    {event.activity}
                    {event.matched_root_cause && (
                      <span className="tag tag--evidence">Root-cause evidence</span>
                    )}
                    {event.is_manual && (
                      <span className="tag tag--manual">Manual</span>
                    )}
                    {event.is_exception && (
                      <span className="tag tag--exception">Exception</span>
                    )}
                    {event.is_rework && (
                      <span className="tag tag--rework">Rework</span>
                    )}
                    {event.waiting_minutes && (
                      <span className="tag tag--waiting">
                        {Math.round(event.waiting_minutes / 60)}h waiting
                      </span>
                    )}
                  </p>
                  <p className="fix-tl__meta">
                    {event.stage && `${event.stage} - `}
                    {event.owner_team || ""}
                  </p>
                </li>
              ))}
            </ol>

          </div>
        ) : (
          <div className="fix-case fix-case--empty">
            <strong>No representative case returned</strong>
            <p>The recommendation is based on aggregate evidence.</p>
          </div>
        )}
      </section>

      <section className="fix-section">
        <header className="fix-section__head">
          <div>
            <span className="package-section__eyebrow">Recommendation plan</span>
            <h2>AI Agents</h2>
          </div>
        </header>

        <p className="ai-plan-summary">
          {recommendation?.recommendation_plan?.summary}
        </p>

        <div className="ai-agent-grid">
          {array(recommendation?.recommendation_plan?.agents).map(
            (agent, index) => (
              <AgentCard
                agent={agent}
                index={index}
                key={agent?.agent_id || index}
              />
            )
          )}
        </div>
      </section>

      <section className="fix-section">
        <details className="package-section__details">
          <summary className="fix-section__head">
            <div>
              <span className="package-section__eyebrow">To-be state design</span>
              <h2>Future-state operating model</h2>
            </div>
          </summary>

          <div className="package-section__content">
            {hasToBeDesign && toBeSummaryCards.length > 0 && (
              <div className="to-be-summary-cards">
                {toBeSummaryCards.map((card) => (
                  <div className="to-be-summary-card" key={card.key}>
                    <span className="to-be-summary-card__label">
                      {card.label}
                    </span>
                    <span className="to-be-summary-card__value">
                      {card.value ?? "-"}
                    </span>
                    {card.sub && (
                      <span className="to-be-summary-card__sub">
                        {card.sub}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {(!toBeLoading || hasToBeDesign) && (
              futureSteps.length ? (
                <ol className="future">
                  {futureSteps.map((step, index) => (
                    <React.Fragment key={step.id || index}>
                      <li
                        className={`future__step${
                          step?.type ? ` future__step--${step.type}` : ""
                        }`}
                      >
                        <span className="future__type">
                          {label(step?.type || "existing")}
                        </span>
                        <span className="future__name">{step?.name}</span>
                        <p className="future__desc">{step?.description}</p>
                      </li>
                      {index < futureSteps.length - 1 && (
                        <li className="future__arrow" aria-hidden="true">
                          <span>→</span>
                        </li>
                      )}
                    </React.Fragment>
                  ))}
                </ol>
              ) : (
                <p className="fix-section__note">
                  No future-state design was returned for this recommendation.
                </p>
              )
            )}

            {hasToBeDesign && eliminatedActivities.length > 0 && (
              <section className="to-be-eliminated">
                <h3>Eliminated non value adding steps</h3>
                <div className="to-be-eliminated__chips">
                  {eliminatedActivities.map((activity, index) => (
                    <span
                      className="to-be-chip"
                      key={
                        activity.id ||
                        activity.to_be_activity ||
                        activity.activity_name ||
                        activity.current_activity ||
                        index
                      }
                    >
                      {activity.to_be_activity ||
                        activity.activity_name ||
                        activity.current_activity ||
                        activity}
                    </span>
                  ))}
                </div>
              </section>
            )}

            {hasToBeDesign && systemNames.length > 0 && (
              <section className="to-be-systems">
                <h3>Systems</h3>
                <div className="to-be-systems__chips">
                  {systemNames.map((name) => (
                    <span className="to-be-chip" key={name}>
                      {name}
                    </span>
                  ))}
                </div>
              </section>
            )}

            {hasToBeDesign && impactRationale.length > 0 && (
              <section className="to-be-impact">
                <h3>Impact &amp; rationale</h3>
                <div className="to-be-impact__grid">
                  {impactRationale.map((item, index) => {
                    const obj =
                      typeof item === "string" || typeof item === "number"
                        ? { summary: String(item) }
                        : item || {};
                    const title =
                      obj.title ||
                      obj.summary ||
                      obj.impact ||
                      obj.label ||
                      `Item ${index + 1}`;
                    const description =
                      obj.rationale ||
                      obj.detail ||
                      obj.description ||
                      obj.reason;
                    return (
                      <article
                        className="to-be-impact-card"
                        key={obj.id || obj.key || index}
                      >
                        <strong className="to-be-impact-card__title">
                          {title}
                        </strong>
                        {description && (
                          <p className="to-be-impact-card__body">
                            {description}
                          </p>
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>
            )}

            {hasToBeDesign && changeMixTotal && (
              <section className="change-mix">
                <h3>Change mix</h3>
                <div className="change-mix__bar">
                  {["ai", "deterministic", "human", "existing"].map(
                    (key) => {
                      const count = mixCounts[key];
                      if (!count) return null;
                      const percent = Math.round((count / changeMixTotal) * 100);
                      return (
                        <div
                          key={key}
                          className={`change-mix__segment change-mix__segment--${key}`}
                          style={{ width: `${percent}%` }}
                        >
                          <span className="change-mix__label">
                            {mixLabel(key)} ({count})
                          </span>
                        </div>
                      );
                    },
                  )}
                </div>
              </section>
            )}

            {toBeLoading && !hasToBeDesign && (
              <p className="fix-section__note">
                <span className="loading-spinner" aria-hidden="true" />{" "}
                Loading To-be state design...
              </p>
            )}

            {toBeError && !toBeLoading && (
              <p className="fix-section__note">
                To-be state design is unavailable: {toBeError.message}
              </p>
            )}

            {hasToBeDesign && (
              <div className="to-be-layout">
            <div className="to-be-summary">
              <h3>To-be summary</h3>
              <p>
                {toBe.to_be_summary ||
                  summary.narrative ||
                  toBe.executive_summary}
              </p>
              <dl className="to-be-inline">
                <div>
                  <dt>Case</dt>
                  <dd>
                    {summary.case_id ||
                      toBe.representative_case?.representative_case_id ||
                      "—"}
                  </dd>
                </div>
                <div>
                  <dt>Stage</dt>
                  <dd>
                    {array(
                      summary.stages_transformed ||
                        toBe.representative_case?.stage_being_transformed
                    ).join("  b7 ") || "—"}
                  </dd>
                </div>
                <div>
                  <dt>Model</dt>
                  <dd>{summary.model || toBe.model || "—"}</dd>
                </div>
                <div>
                  <dt>Activities</dt>
                  <dd>
                    {summary.current_activity_count != null &&
                    summary.to_be_activity_count != null
                      ? `${summary.current_activity_count} current  b7 ${summary.to_be_activity_count} to-be`
                      : "—"}
                  </dd>
                </div>
                <div>
                  <dt>Reduced steps</dt>
                  <dd>
                    {summary.reduced_steps != null
                      ? summary.reduced_steps
                      : "—"}
                  </dd>
                </div>
                {tier1Kpi && (
                  <div>
                    <dt>Tier 1 KPI</dt>
                    <dd>
                      {tier1Kpi.kpi_id
                        ? `${tier1Kpi.kpi_id} ${tier1Kpi.kpi_name || ""}`.trim()
                        : tier1Kpi.kpi_name || "—"}
                    </dd>
                  </div>
                )}
                {!!tier2Kpis.length && (
                  <div>
                    <dt>Tier 2 KPIs</dt>
                    <dd>
                      {tier2Kpis
                        .map((kpi) =>
                          typeof kpi === "string"
                            ? kpi
                            : kpi.kpi_id
                            ? `${kpi.kpi_id} ${kpi.kpi_name || ""}`.trim()
                            : kpi.kpi_name || ""
                        )
                        .filter(Boolean)
                        .join("  b7 ") || "—"}
                    </dd>
                  </div>
                )}
                {!!gaps.length && (
                  <div>
                    <dt>Gaps / challenges</dt>
                    <dd>
                      {gaps
                        .map((gap) =>
                          gap.gap_reference && gap.challenge
                            ? `${gap.gap_reference} ${gap.challenge}`
                            : gap.gap_reference ||
                              gap.challenge ||
                              gap.challenge_section ||
                              ""
                        )
                        .filter(Boolean)
                        .join("  b7 ") || "—"}
                    </dd>
                  </div>
                )}
                {rootCauseContext && (
                  <div>
                    <dt>Root cause</dt>
                    <dd>
                      {rootCauseContext.title ||
                        rootCauseContext.description ||
                        text(rootCauseContext)}
                    </dd>
                  </div>
                )}
              </dl>
            </div>

            <div className="to-be-metrics">
              <h3>Current vs To-be</h3>
              <div className="to-be-metrics__grid">
                <div>
                  <span className="to-be-metrics__label">Current state</span>
                  <ul>
                    {comparison
                      ? [
                          "number_of_activities",
                          "manual_activities",
                          "ai_agent_activities",
                          "deterministic_automation_activities",
                          "human_approval_activities",
                          "rework_activities",
                          "waiting_activities",
                          "handoffs",
                          "systems_touched",
                        ].map((key) => {
                          const labels = {
                            number_of_activities: "Activities",
                            manual_activities: "Manual activities",
                            ai_agent_activities: "AI agent activities",
                            deterministic_automation_activities:
                              "Deterministic automation",
                            human_approval_activities:
                              "Human approval activities",
                            rework_activities: "Rework activities",
                            waiting_activities: "Waiting activities",
                            handoffs: "Handoffs",
                            systems_touched: "Systems touched",
                          };
                          const bucket = comparison[key] || {};
                          const currentValue = Array.isArray(bucket.current)
                            ? bucket.current.join("  b7 ")
                            : bucket.current;
                          return (
                            <li key={key}>
                              <strong>{currentValue ?? "—"}</strong>
                              <span>{labels[key]}</span>
                            </li>
                          );
                        })
                      : (
                        <>
                          <li>
                            <strong>
                              {toBe.current_state_metrics?.activity_count ??
                                "—"}
                            </strong>
                            <span>Activities</span>
                          </li>
                          <li>
                            <strong>
                              {toBe.current_state_metrics?.manual_activity_count ??
                                "—"}
                            </strong>
                            <span>Manual</span>
                          </li>
                          <li>
                            <strong>
                              {toBe.current_state_metrics?.rework_activity_count ??
                                "—"}
                            </strong>
                            <span>Rework</span>
                          </li>
                        </>
                      )}
                  </ul>
                </div>
                <div>
                  <span className="to-be-metrics__label">To-be design</span>
                  <ul>
                    {comparison
                      ? [
                          "number_of_activities",
                          "manual_activities",
                          "ai_agent_activities",
                          "deterministic_automation_activities",
                          "human_approval_activities",
                          "rework_activities",
                          "waiting_activities",
                          "handoffs",
                          "systems_touched",
                        ].map((key) => {
                          const labels = {
                            number_of_activities: "Activities",
                            manual_activities: "Manual activities",
                            ai_agent_activities: "AI agent activities",
                            deterministic_automation_activities:
                              "Deterministic automation",
                            human_approval_activities:
                              "Human approval activities",
                            rework_activities: "Rework activities",
                            waiting_activities: "Waiting activities",
                            handoffs: "Handoffs",
                            systems_touched: "Systems touched",
                          };
                          const bucket = comparison[key] || {};
                          const toBeValue = Array.isArray(bucket.to_be)
                            ? bucket.to_be.join("  b7 ")
                            : bucket.to_be;
                          return (
                            <li key={key}>
                              <strong>{toBeValue ?? "—"}</strong>
                              <span>{labels[key]}</span>
                            </li>
                          );
                        })
                      : (
                        <>
                          <li>
                            <strong>
                              {toBe.to_be_metrics?.to_be_activity_count ??
                                "—"}
                            </strong>
                            <span>Activities</span>
                          </li>
                          <li>
                            <strong>
                              {toBe.to_be_metrics?.reduced_activity_count ??
                                "—"}
                            </strong>
                            <span>Reduced / removed</span>
                          </li>
                          <li>
                            <strong>
                              {toBe.to_be_metrics?.activity_reduction_pct !=
                              null
                                ? `${toBe.to_be_metrics.activity_reduction_pct}%`
                                : "—"}
                            </strong>
                            <span>Reduction</span>
                          </li>
                        </>
                      )}
                  </ul>
                </div>
              </div>
              {toBe.current_vs_to_be && (
                <dl className="to-be-inline">
                  <div>
                    <dt>Reduced steps</dt>
                    <dd>{toBe.current_vs_to_be.reduced_steps ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Remaining human-controlled</dt>
                    <dd>
                      {array(
                        toBe.current_vs_to_be
                          .activities_remaining_human_controlled
                      )
                        .map(
                          (activity) =>
                            activity.to_be_activity ||
                            activity.activity_name
                        )
                        .join("  b7 ") || "—"}
                    </dd>
                  </div>
                </dl>
              )}
            </div>

            {agenticWorkflow.length > 0 && (
              <div className="to-be-table">
                <details>
                  <summary>Agentic workflow</summary>
                  <table>
                    <thead>
                      <tr>
                        <th>Seq</th>
                        <th>Agent</th>
                        <th>Trigger</th>
                        <th>Action</th>
                        <th>Systems</th>
                        <th>Human in loop</th>
                      </tr>
                    </thead>
                    <tbody>
                      {agenticWorkflow.map((step) => (
                        <tr
                          key={
                            step.agent_id ||
                            step.agent_name ||
                            step.sequence_order
                          }
                        >
                          <td>{step.sequence_order ?? "—"}</td>
                          <td>
                            {step.agent_name || step.agent_id || "—"}
                          </td>
                          <td>
                            {array(step.trigger).join("  b7 ") || "—"}
                          </td>
                          <td>
                            {array(step.action).join("  b7 ") || "—"}
                          </td>
                          <td>
                            {array(
                              step.systems_accessed || step.systems || []
                            ).join("  b7 ") || "—"}
                          </td>
                          <td>
                            {step.human_in_the_loop
                              ? step.human_in_the_loop_detail || "Yes"
                              : "No"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </div>
            )}

            {traceabilityRows.length > 0 && (
              <div className="to-be-table">
                <details>
                  <summary>Traceability</summary>
                  <table>
                    <thead>
                      <tr>
                        <th>Current activity</th>
                        <th>To-be activity</th>
                        <th>Change type</th>
                        <th>Recommended agent</th>
                        <th>Gap / KPI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {traceabilityRows.map((row, index) => (
                        <tr
                          key={
                            row.current_activity ||
                            row.to_be_activity ||
                            index
                          }
                        >
                          <td>{row.current_activity || "—"}</td>
                          <td>{row.to_be_activity || "—"}</td>
                          <td>{row.change_type || "—"}</td>
                          <td>{row.recommended_agent || "—"}</td>
                          <td>
                            {[
                              row.gap,
                              row.problem_root_cause,
                              row.tier_1_kpi,
                              row.tier_2_kpi,
                            ]
                              .filter(Boolean)
                              .join("  b7 ") || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </div>
            )}

            {toBe.diagram_path && (
              <div className="to-be-summary">
                <h3>Diagram path</h3>
                <dl className="to-be-inline">
                  <div>
                    <dt>Path</dt>
                    <dd>{toBe.diagram_path}</dd>
                  </div>
                </dl>
              </div>
            )}

            {toBe.summary && (
              <div className="to-be-summary">
                <h3>Summary</h3>
                <dl className="to-be-inline">
                  <div>
                    <dt>Activities</dt>
                    <dd>
                      {toBe.summary.current_activity_count} current → 
                      {toBe.summary.to_be_activity_count} to-be
                    </dd>
                  </div>
                  <div>
                    <dt>Reduced steps</dt>
                    <dd>{toBe.summary.reduced_steps ?? "—"}</dd>
                  </div>
                  <div>
                    <dt>Narrative</dt>
                    <dd>{toBe.summary.narrative}</dd>
                  </div>
                </dl>
              </div>
            )}

            {diagramSrc && (
              <details className="to-be-diagram">
                <summary>Stage flowchart</summary>
                <figure>
                  <img
                    src={diagramSrc}
                    alt="To-be stage design diagram"
                  />
                </figure>
              </details>
            )}
          </div>
        )}
          </div>
        </details>
      </section>

      <section className="fix-section ai-next-step">
        <div>
          <span className="package-section__eyebrow">Next step</span>
          <h2>Ready to create the deployment package?</h2>
          <p className="fix-section__note">
            Turn this recommendation into a reviewable loan deployment package.
          </p>
        </div>

        {error && (
          <p className="fix-error" role="alert">
            {error.message}
          </p>
        )}

        <button
          type="button"
          className="btn-primary"
          disabled={agentLoading}
          onClick={fixWithAgent}
        >
          {agentLoading && (
            <span className="loading-spinner" aria-hidden="true" />
          )}
          {agentLoading ? "Loading deployment package..." : "✦ Fix with Agent"}
        </button>
      </section>
    </main>
  );
}



