import { Link, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";

import { createDeploymentPackage, getDeploymentPackage, requestDeploymentApproval, resetDeploymentApproval } from "../api/client.js";
import "../fixwithai.css";

export const sections = [
  ["executive_summary", "Executive summary"],
  ["transformation_overview", "Transformation overview"],
  ["agents", "AI agents"],
  ["business_rules", "Business rules"],
  ["integrations", "Integrations"],
  ["architecture", "Architecture"],
  ["codebase", "Agent codebase"],
  ["deployment_config", "Deployment configuration"],
  ["approval_workflow", "Approval workflow"],
];

function label(key) {
  return key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function Value({ value }) {
  if (Array.isArray(value)) return <ul className="package-list">{value.map((item, i) => <li key={i}>{typeof item === "object" && item !== null ? <Value value={item} /> : String(item ?? "—")}</li>)}</ul>;
  if (value && typeof value === "object") return <div className="package-inline-list">{Object.entries(value).map(([key, item]) => <div key={key}><strong>{label(key)}</strong><Value value={item} /></div>)}</div>;
  return <span>{String(value ?? "—")}</span>;
}

function FieldCards({ value }) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return <Value value={value} />;
  return <div className="package-cards">{Object.entries(value).map(([key, item]) => (
    <article className="package-card" key={key}>
      <h3>{label(key)}</h3>
      <div className="package-card__value"><Value value={item} /></div>
    </article>
  ))}</div>;
}

function DetailGrid({ value }) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return <Value value={value} />;
  return <div className="package-detail-grid">{Object.entries(value).map(([key, item]) => (
    <article className="package-detail" key={key}>
      <h3>{label(key)}</h3>
      <div><Value value={item} /></div>
    </article>
  ))}</div>;
}

function CodeFiles({ value }) {
  const files = Array.isArray(value?.generated_files) ? value.generated_files : [];
  if (!files.length) return <Value value={value} />;

  const normalized = files.map((file, index) => ({
    path: file.path || `generated-agent-${index + 1}`,
    language: file.language || "text",
    purpose: file.purpose,
    code: file.code || "No generated code was returned.",
  }));

  const [selectedIndex, setSelectedIndex] = useState(0);
  const selected = normalized[selectedIndex] || normalized[0];

  return (
    <div className="code-files">
      <div className="code-split">
        <nav className="code-tree" aria-label="Agent code files">
          <div className="code-tree__title">Agent codebase</div>
          <ul className="code-tree__list">
            {normalized.map((file, index) => {
              const depth = (file.path || "").split(/[\\/]/).length - 1;
              const isActive = index === selectedIndex;
              return (
                <li className="code-tree__item" key={file.path || index}>
                  <button
                    type="button"
                    className={"code-tree__button" + (isActive ? " code-tree__button--active" : "")}
                    onClick={() => setSelectedIndex(index)}
                    style={{ paddingLeft: 12 + depth * 12 }}
                  >
                    <span className="code-tree__file">{file.path}</span>
                    <span className="code-tree__lang">{file.language}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <article className="code-file code-file--viewer">
          <header>
            <strong>{selected.path}</strong>
            <span>{selected.language}</span>
          </header>
          {selected.purpose && <p>{selected.purpose}</p>}
          <pre><code>{selected.code}</code></pre>
        </article>
      </div>
    </div>
  );
}

function buildDeploymentFiles(value, artifacts) {
  const files = [];

  const addFile = (path, content) => {
    if (!path || !content) return;
    files.push({ path, content });
  };

  if (Array.isArray(value?.files)) {
    value.files.forEach((file, index) => {
      addFile(file.path || `deployment-${index + 1}.yaml`, file.content || file.yaml || "");
    });
  }

  if (typeof value === "string") {
    addFile("deployment.yaml", value);
  } else if (value && typeof value === "object") {
    Object.entries(value).forEach(([key, item]) => {
      if (typeof item === "string") {
        addFile(key, item);
      } else if (item && typeof item === "object") {
        const content = item.content || item.yaml || item.value;
        if (typeof content === "string") addFile(key, content);
      }
    });
  }

  if (artifacts && typeof artifacts === "object") {
    if (typeof artifacts.deployment_yaml === "string") {
      addFile("deployment.yaml", artifacts.deployment_yaml);
    }
    if (Array.isArray(artifacts.yaml_files)) {
      artifacts.yaml_files.forEach((file, index) => {
        addFile(file.path || `deployment-${index + 1}.yaml`, file.content || file.yaml || "");
      });
    }
    Object.entries(artifacts).forEach(([key, item]) => {
      if (key.endsWith(".yaml") && typeof item === "string") {
        addFile(key, item);
      }
    });
  }

  return files;
}

function DeploymentConfigSection({ value, artifacts }) {
  const files = buildDeploymentFiles(value, artifacts);
  if (!files.length) return <DetailGrid value={value} />;

  const [selectedIndex, setSelectedIndex] = useState(0);
  const selected = files[selectedIndex] || files[0];

  return (
    <div className="code-files">
      <div className="code-split">
        <nav className="code-tree" aria-label="Deployment configuration files">
          <div className="code-tree__title">Deployment YAML</div>
          <ul className="code-tree__list">
            {files.map((file, index) => {
              const depth = (file.path || "").split(/[\\/]/).length - 1;
              const isActive = index === selectedIndex;
              return (
                <li className="code-tree__item" key={file.path || index}>
                  <button
                    type="button"
                    className={"code-tree__button" + (isActive ? " code-tree__button--active" : "")}
                    onClick={() => setSelectedIndex(index)}
                    style={{ paddingLeft: 12 + depth * 12 }}
                  >
                    <span className="code-tree__file">{file.path}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <article className="code-file code-file--viewer">
          <header>
            <strong>{selected.path}</strong>
            <span>yaml</span>
          </header>
          <pre><code>{selected.content}</code></pre>
        </article>
      </div>
    </div>
  );
}

export function SectionBody({ section, value, artifacts }) {
  if (section === "agents") {
    const agents = Array.isArray(value) ? value : (value && typeof value === "object" ? Object.values(value) : []);
    return <div className="agent-cards">{agents.map((agent, index) => (
      <article className="agent package-agent" key={agent?.id || agent?.agent_id || index}>
        <div className="agent__head"><span className="agent__icon">✦</span><div><h3 className="agent__name">{agent?.name || `AI agent ${index + 1}`}</h3><span className="agent__id">{agent?.id || agent?.agent_id || "governed-agent"}</span></div></div>
        <p className="package-agent__purpose">{agent?.responsibility || agent?.purpose || "Supports governed loan operations."}</p>
        <div className="package-agent__details">
          <div><span>Trigger</span><Value value={agent?.trigger || agent?.triggers || "Defined by loan process events"} /></div>
          <div><span>Actions</span><Value value={agent?.action || agent?.actions || "Prepare a recommendation for review"} /></div>
          <div><span>Expected outcome</span><Value value={agent?.expected_outcome || "Consistent, auditable loan handling"} /></div>
        </div>
      </article>
    ))}</div>;
  }
  if (section === "business_rules") {
    const raw = value;
    const rules = Array.isArray(raw)
      ? raw
      : raw && typeof raw === "object"
        ? (Array.isArray(raw.rules) ? raw.rules : Object.values(raw))
        : [];

    if (!rules.length) return <FieldCards value={value} />;

    return (
      <div className="business-rule-grid">
        {rules.map((rule, index) => {
          const normalized = (rule && typeof rule === "object") ? rule : { name: String(rule ?? "Business rule") };
          const name = normalized.name || normalized.title || normalized.rule || normalized.Rule || `Business rule ${index + 1}`;
          const ruleId = normalized.rule_id || normalized.id || null;
          const agentId = normalized.agent_id || normalized.agent || normalized.agentId || null;
          const ifValue = normalized.if || normalized.condition || normalized.conditions;
          const thenValue = normalized.then || normalized.action || normalized.actions;
          const approval = normalized.approval_required ?? normalized.approval ?? normalized.human_in_loop;
          const exception = normalized.exception_handling || normalized.exceptions;
          const notes = normalized.notes || normalized.rationale;

          return (
            <article className="business-rule" key={ruleId || agentId || index}>
              <header>
                <span className="package-sequence__number">{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <h3>{name}</h3>
                  {(ruleId || agentId) && (
                    <div className="business-rule__meta">
                      {ruleId && <span>{ruleId}</span>}
                      {ruleId && agentId && <span> · </span>}
                      {agentId && <span>{agentId}</span>}
                    </div>
                  )}
                </div>
              </header>
              <dl className="business-rule__dl">
                {ifValue && (
                  <div>
                    <dt>IF</dt>
                    <dd><Value value={ifValue} /></dd>
                  </div>
                )}
                {thenValue && (
                  <div>
                    <dt>THEN</dt>
                    <dd><Value value={thenValue} /></dd>
                  </div>
                )}
                {approval !== undefined && approval !== null && (
                  <div>
                    <dt>Approval required</dt>
                    <dd><Value value={approval} /></dd>
                  </div>
                )}
                {exception && (
                  <div>
                    <dt>Exception handling</dt>
                    <dd><Value value={exception} /></dd>
                  </div>
                )}
                {notes && (
                  <div>
                    <dt>Notes</dt>
                    <dd><Value value={notes} /></dd>
                  </div>
                )}
              </dl>
            </article>
          );
        })}
      </div>
    );
  }
  if (section === "integrations") {
    const integrations = Array.isArray(value)
      ? value
      : value && typeof value === "object"
        ? Object.values(value)
        : [];

    if (!integrations.length) return <FieldCards value={value} />;

    const normalized = integrations.map((integration, index) => {
      const system =
        integration?.system || integration?.system_name || integration?.name || `System ${index + 1}`;
      const slug = String(system).toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const envPrefix = String(system)
        .toUpperCase()
        .replace(/[^A-Z0-9]+/g, "_");

      return {
        system,
        endpoint: integration?.endpoint || `/api/v1/<${slug}>/events`,
        envVars: [
          `$${envPrefix}_API_URL`,
          `$${envPrefix}_API_TOKEN`,
        ],
      };
    });

    return (
      <div className="integration-list">
        <div className="integration-grid">
          {normalized.map((row) => (
            <div className="integration-grid__row" key={row.system}>
              <h3>{row.system}</h3>
              <dl>
                <div>
                  <dt>API endpoint</dt>
                  <dd>
                    <code>POST {row.endpoint}</code>
                  </dd>
                </div>
                <div>
                  <dt>Environment variables</dt>
                  <dd>
                    {row.envVars.map((env) => (
                      <div key={env}>
                        <code>{env}</code>
                      </div>
                    ))}
                  </dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (section === "executive_summary") {
    const summary = value || {};
    const parts = [];

    if (summary.business_problem) {
      parts.push(`Business problem. ${summary.business_problem}`);
    }
    if (summary.metrics_attention) {
      parts.push(summary.metrics_attention);
    }
    if (summary.agents_overview) {
      parts.push(summary.agents_overview);
    }
    if (summary.recommendation) {
      parts.push(`Recommendation. ${summary.recommendation}`);
    }
    if (Array.isArray(summary.expected_benefits) && summary.expected_benefits.length) {
      parts.push(`Expected benefits: ${summary.expected_benefits.join(", ")}.`);
    }

    const text = summary.narrative || parts.join(" ");

    return (
      <div className="package-summary">
        <p className="package-summary__text">
          {text || "Executive summary will be finalized during review."}
        </p>
      </div>
    );
  }
  if (section === "codebase") return <CodeFiles value={value} />;
  if (section === "deployment_config") return <DeploymentConfigSection value={value} artifacts={artifacts} />;
  if (section === "transformation_overview") {
    const overview = value || {};
    const parts = [];

    if (overview.process) {
      parts.push(`Process. ${overview.process}.`);
    }
    if (overview.scope) {
      parts.push(`Scope. ${overview.scope}.`);
    }
    if (overview.current_state) {
      parts.push(`Current state. ${overview.current_state}.`);
    }
    if (overview.target_state) {
      parts.push(`Target state. ${overview.target_state}.`);
    }
    if (overview.agents_overview) {
      parts.push(overview.agents_overview);
    }

    const text = overview.narrative || parts.join(" ");

    return (
      <div className="package-summary">
        <p className="package-summary__text">
          {text || "Transformation overview will be finalized during review."}
        </p>
      </div>
    );
  }
  if (["architecture", "approval_workflow"].includes(section)) {
    return <DetailGrid value={value} />;
  }
  return <FieldCards value={value} />;
}

export default function DeploymentPackagePage() {
  const { state } = useLocation();
  const [result, setResult] = useState(state?.packageResult || null);
  const [status, setStatus] = useState(state?.packageResult ? "" : "Generating deployment package…");
  const [error, setError] = useState(null);
  const [approvalMessage, setApprovalMessage] = useState("");
  const [approvalBusy, setApprovalBusy] = useState(false);

  useEffect(() => {
    if (state?.packageResult) return undefined;
    const controller = new AbortController();
    createDeploymentPackage({ kpi: state?.kpi || {}, root_cause: state?.cause || {}, evidence: state?.cause?.statistics || [] }, { signal: controller.signal })
      .then((response) => { setResult(response); setStatus(""); })
      .catch((cause) => { if (cause.name !== "AbortError") { setError(cause.message); setStatus(""); } });
    return () => controller.abort();
  }, [state]);

  const packageData = result?.package || result || {};
  const content = result?.generated_content || packageData.generated_content;
  const artifacts = result?.package_artifacts || content?.artifacts || {};
  const mailConfigured = result?.mail_configured ?? false;

  const approvals = packageData?.approvals || {};
  const approvalStatus = (approvals.status || "NOT_REQUESTED").toUpperCase();

  const approvalStateLabel =
    approvalStatus === "APPROVED"
      ? "Approved"
      : approvalStatus === "REJECTED"
      ? "Changes requested"
      : approvalStatus === "PENDING" || approvalStatus === "REQUESTED"
      ? "In review"
      : "Not requested";

  const approvalProgress = approvalStatus === "APPROVED" ? 100 : 0;

  const stageTitle =
    state?.kpi?.kpi_name || content?.kpi_context?.tier_1_kpi?.kpi_name || "Corporate loan process";

  const stakeholders =
    approvals.items && Array.isArray(approvals.items) && approvals.items.length
      ? approvals.items.map((item, index) => ({
          id: item.id || index,
          title: item.title || item.role || `Stakeholder ${index + 1}`,
          email: item.email || item.address || approvals.reviewer || "stakeholder@example.com",
        }))
      : [
          {
            id: "process-lead",
            title: `Process lead – ${stageTitle}`,
            email: approvals.reviewer || "process.lead@corp.com",
          },
          {
            id: "risk-lead",
            title: "Risk & controls lead – Corporate lending",
            email: "risk.controls@corp.com",
          },
          {
            id: "automation-lead",
            title: "Automation CoE lead – AI agents",
            email: "automation.coe@corp.com",
          },
        ];

  const approvedRoles = approvalStatus === "APPROVED" ? stakeholders.length : 0;

  if (error) return <main className="dash fix-page"><Link className="fix-breadcrumb" to="/">← KPI Health</Link><section className="fix-error"><h2>Unable to generate deployment package</h2><p>{error}</p></section></main>;  if (!content) return <main className="dash fix-page"><p className="fix-loading">{status}</p></main>;

  async function approve() {
    if (!packageData?.package_id) {
      setApprovalMessage("Approval is only available for saved deployment packages.");
      return;
    }
    setApprovalBusy(true);
    setApprovalMessage("Submitting approval request…");
    try {
      const response = await requestDeploymentApproval(packageData.package_id);
      setResult((current) => ({
        ...current,
        approvals: { ...(current?.approvals || {}), status: response.status },
        package: current?.package ? { ...current.package, approvals: { ...(current.package.approvals || {}), status: response.status } } : current?.package,
      }));
      setApprovalMessage(response?.message || "Approval request submitted.");
    } catch (cause) {
      setApprovalMessage(cause.message);
    } finally {
      setApprovalBusy(false);
    }
  }

  async function resetApproval() {
    if (!packageData?.package_id) {
      setApprovalMessage("No saved deployment package to reset.");
      return;
    }
    setApprovalBusy(true);
    setApprovalMessage("Resetting approval status…");
    try {
      const response = await resetDeploymentApproval(packageData.package_id);
      setResult((current) => ({
        ...current,
        approvals: { ...(current?.approvals || {}), status: response.status },
        package: current?.package ? { ...current.package, approvals: { ...(current.package.approvals || {}), status: response.status } } : current?.package,
      }));
      setApprovalMessage(response.message || "Approval status reset.");
    } catch (cause) {
      setApprovalMessage(cause.message);
    } finally {
      setApprovalBusy(false);
    }
  }

  function downloadMarkdown() {
    const markdown = artifacts.markdown;
    if (!markdown) return;
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${packageData?.package_id || "transformation-deployment-package"}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="dash fix-page deployment-package">
      <nav className="fix-breadcrumb"><Link to="/">KPI Health &amp; Root Causes</Link><span>›</span><span>Deployment package</span></nav>
      <header className="fix-head">
        <div>
          <h1>Transformation Deployment Package Approval</h1>
        </div>
        <div className="package-header-actions">
          <button
            className="btn-icon"
            type="button"
            onClick={downloadMarkdown}
            disabled={!artifacts.markdown}
            title="Download transformation deployment package as Markdown"
            aria-label="Download transformation deployment package"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 18 18"
              aria-hidden="true"
              focusable="false"
            >
              <path
                d="M9 2.5v7"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M6 8.5l3 3 3-3"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M4 13.5h10"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </header>
      <div className="deployment-layout">
        <div className="package-section-grid">
          {sections.map(([key, title], index) => (
            <section className={`fix-section package-section package-section--${key}`} key={key}>
              <details className="package-section__details" open={key === "executive_summary" || key === "agents"}>
                <summary className="fix-section__head">
                  <div>
                    <span className="package-section__eyebrow">Section {index + 1}</span>
                    <h2>{title}</h2>
                  </div>
                </summary>
                <div className="package-section__content">
                  <SectionBody section={key} value={content[key]} artifacts={artifacts} />
                </div>
              </details>
            </section>
          ))}
        </div>
        <aside className="fix-section package-approval-card">
          <header className="package-approval-card__head">
            <div className="approval-overview">
              <span className="package-section__eyebrow">Approval status</span>
              <div className="approval-ring">
                <span className="approval-ring__value">{approvalProgress}%</span>
                <span className="approval-ring__status">{approvalStateLabel}</span>
              </div>
            </div>
          </header>

          <div className="package-approval-card__body">
            <section className="approval-stakeholders">
              <h3 className="approval-stakeholders__title">Stakeholder approval</h3>
              <ol className="approval-stakeholders__list">
                {stakeholders.map((stakeholder) => (
                  <li className="approval-stakeholder" key={stakeholder.id}>
                    <div className="approval-stakeholder__rail">
                      <span className="approval-stakeholder__dot" />
                    </div>
                    <div className="approval-stakeholder__content">
                      <span className="approval-stakeholder__role">{stakeholder.title}</span>
                      {stakeholder.email && (
                        <span className="approval-stakeholder__email">
                          {stakeholder.email}
                        </span>
                      )}
                      <span className="approval-stakeholder__status">
                        {approvalStatus === "APPROVED" ? "Approved" : "Pending review"}
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            </section>

            <section className="approval-summary">
              <dl>
                <div>
                  <dt>Required roles</dt>
                  <dd>{stakeholders.length}</dd>
                </div>
                <div>
                  <dt>Approved roles</dt>
                  <dd>{approvedRoles}</dd>
                </div>
                <div>
                  <dt>Package status</dt>
                  <dd>{approvalStateLabel}</dd>
                </div>
              </dl>
            </section>
          </div>

          <div className="approval-controls">
            <button
              className="btn-primary approval-controls__primary"
              type="button"
              onClick={approve}
              disabled={approvalBusy || approvalStatus === "APPROVED"}
            >
              {approvalBusy && <span className="loading-spinner" aria-hidden="true" />}
              {approvalStatus === "NOT_REQUESTED" ? "Request approvals" : "Awaiting approvals"}
            </button>
            <button
              className="btn-secondary approval-controls__secondary"
              type="button"
              onClick={async () => {
                if (!packageData?.package_id) return;
                try {
                  const refreshed = await getDeploymentPackage(packageData.package_id);
                  setResult(refreshed);
                  setApprovalMessage("");
                } catch (cause) {
                  setApprovalMessage(cause.message);
                }
              }}
              disabled={approvalBusy || !packageData?.package_id}
            >
              Refresh status
            </button>
          </div>

          {approvalMessage && (
            <p className="fix-section__note" role="status">
              {approvalMessage}
            </p>
          )}
        </aside>
      </div>
    </main>
  );
}
