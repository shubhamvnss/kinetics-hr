import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { decideApproval, lookupApprovalToken } from "../api/client.js";
import { SectionBody, sections } from "./DeploymentPackage.jsx";
import "../fixwithai.css";

export default function ApprovalPage() {
  const { token } = useParams();
  const [approval, setApproval] = useState(null);
  const [status, setStatus] = useState("Loading approval request…");
  const [error, setError] = useState(null);
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");

  useEffect(() => {
    lookupApprovalToken(token)
      .then((response) => {
        setApproval(response);
        setStatus("");
      })
      .catch((cause) => {
        setError(cause.message);
        setStatus("");
      });
  }, [token]);

  async function submit(decision) {
    setStatus(`Recording ${decision.toLowerCase()} decision…`);
    setError(null);
    try {
      const response = await decideApproval(token, {
        token,
        decision,
        reviewer: reviewer || null,
        comment: comment || null,
      });
      setApproval(response);
      setStatus("Decision recorded.");
    } catch (cause) {
      setError(cause.message);
      setStatus("");
    }
  }

  return (
    <main className="dash fix-page">
      <nav className="fix-breadcrumb">
        <Link to="/">KPI Health &amp; Root Causes</Link>
        <span aria-hidden="true">›</span>
        <span>Approval</span>
      </nav>
      <section className="fix-section">
        <h1>Corporate Loan Deployment Approval</h1>
        {error && <p className="fix-error__message" role="alert">{error}</p>}
        {status && <p className="fix-section__note" role="status">{status}</p>}
        {approval && (
          <>
            <header className="fix-head approval-header">
              <div>
                <h1>
                  AI Transformation Deployment Package
                  {approval.generated_content?.executive_summary?.business_problem ? ` - ${approval.generated_content.executive_summary.business_problem}` : ""}
                  {approval.version != null ? ` (v${approval.version}.0)` : ""}
                </h1>
                <p className="dash__sub">{approval.package_id}</p>
              </div>
              <span className="pill pill--high">{approval.status}</span>
            </header>
            <div className="package-section-grid">
              {sections.map(([key, title], index) => <section className={`fix-section package-section package-section--${key}`} key={key}><header className="fix-section__head"><div><span className="package-section__eyebrow">Section {index + 1}</span><h2>{title}</h2></div><span className="fix-section__note">Approval review detail</span></header><SectionBody section={key} value={approval.generated_content?.[key]} /></section>)}
            </div>
            {approval.status === "PENDING" && (
              <>
                <section className="fix-section approval-decision">
                  <div><span className="package-section__eyebrow">Approval decision</span><h2>Review and record decision</h2><p className="fix-section__note">Your decision will be recorded against this deployment package.</p></div>
                <label className="package-field">
                  Reviewer
                  <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} />
                </label>
                <label className="package-field">
                  Comment
                  <textarea value={comment} onChange={(event) => setComment(event.target.value)} />
                </label>
                <div className="package-actions">
                  <button className="btn-primary" type="button" onClick={() => submit("APPROVED")}>Approve</button>
                  <button className="btn-secondary" type="button" onClick={() => submit("REJECTED")}>Reject</button>
                </div>
                </section>
              </>
            )}
          </>
        )}
      </section>
    </main>
  );
}
