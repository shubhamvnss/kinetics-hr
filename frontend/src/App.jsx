import { useEffect, useState } from "react";
import React from "react";
import { BrowserRouter, Route, Routes, useNavigate, NavLink } from "react-router-dom";

import KpiDashboard from "./KpiDashboard.jsx";
import FixWithAIPage from "./pages/FixWithAI.jsx";
import DeploymentPackagePage from "./pages/DeploymentPackage.jsx";
import ApprovalPage from "./pages/ApprovalPage.jsx";
import { getFixWithAI, getRootCauseAnalysis, getTier1Health } from "./api/client.js";
import "./dashboard.css";

class AppErrorBoundary extends React.Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  render() {
    if (this.state.error) return <main className="dash"><section className="fix-error" role="alert">
      <h1>Something went wrong rendering this view</h1>
      <p>{this.state.error.message}</p><button className="btn-secondary" onClick={() => window.location.reload()}>Reload</button>
    </section></main>;
    return this.props.children;
  }
}


function normalizeAnalysis(analysis) {
  const summary = analysis?.frontend_summary?.analysis_summary;

  if (!summary) {
    return null;
  }

  return {
    ...summary,
    critical_kpis: (summary.critical_kpis || []).map((kpi) => ({
      ...kpi,
      // The Kinetic API already returns ranked, evidence-backed causes in this
      // property. Preserve the complete objects instead of reducing them to
      // the legacy likely_drivers fallback.
      top_root_causes: (kpi.top_root_causes || kpi.likely_drivers || []).map(
        (cause, index) => {
          if (typeof cause === "string") {
            return {
              rank: index + 1,
              root_cause: cause,
              root_cause_summary: cause,
              confidence: null,
              statistics: [],
              affected_cases: {},
              linked_tier2_kpis: [],
              gap_references: [],
            };
          }

          return {
            ...cause,
            rank: cause.rank ?? index + 1,
            root_cause: cause.root_cause || cause.title || "Unspecified root cause",
            root_cause_summary:
              cause.root_cause_summary ||
              cause.causal_explanation ||
              cause.description ||
              "",
            statistics: cause.statistics || cause.impact_statistics || [],
            affected_cases: cause.affected_cases || {},
            linked_tier2_kpis: cause.linked_tier2_kpis || [],
            gap_references: cause.gap_references || [],
          };
        }
      ),
    })),
  };
}


function DashboardPage() {
  const navigate = useNavigate();
  const [state, setState] = useState({
    status: "loading",
    tier1: [],
    analysis: null,
    runStats: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();

    async function loadDashboard() {
      try {
        const [health, analysis] = await Promise.all([
          getTier1Health({ signal: controller.signal }),
          getRootCauseAnalysis({ signal: controller.signal }),
        ]);

        setState({
          status: "ready",
          tier1: health?.tier1_all || [],
          runStats: health?.run_stats || null,
          analysis: normalizeAnalysis(analysis),
          error: null,
        });
      } catch (error) {
        if (error.name === "AbortError") {
          return;
        }

        setState((current) => ({
          ...current,
          status: "error",
          error,
        }));
      }
    }

    loadDashboard();

    return () => controller.abort();
  }, []);

  if (state.status === "loading") {
    return <p className="empty empty--page">Loading Kinetic analytics…</p>;
  }

  if (state.status === "error") {
    return (
      <main className="dash">
        <section className="fix-error" role="alert">
          <h1>Unable to load Kinetic analytics</h1>
          <p>{state.error?.message}</p>
          <p>
            Confirm that the backend is running and that Azure OpenAI and PostgreSQL
            are available.
          </p>
        </section>
      </main>
    );
  }

  const navigateToFix = async (kpi, cause) => {
    const rootCauseId = `${kpi.kpi_id}-${cause.rank}`;
    const recommendation = await getFixWithAI(rootCauseId);
    navigate(`/root-cause/${rootCauseId}/fix-with-ai`, {
      state: { kpi, cause, recommendation },
    });
  };

  return (
    <KpiDashboard
      tier1={state.tier1}
      analysis={state.analysis}
      runStats={state.runStats}
      isSample={false}
      onFixWithAI={navigateToFix}
    />
  );
}


export default function App() {
  return (
    <AppErrorBoundary>
      <BrowserRouter>
        <div className="app-shell">
          <header className="app-nav" aria-label="Deloitte Kinetic navigation">
            <div className="app-nav__brand">
              <span className="app-nav__logo-dot" aria-hidden="true" />
              <span className="app-nav__title">Deloitte Kinetic</span>
              <span className="app-nav__product">AI Transformation</span>
            </div>
          </header>

          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/root-cause/:rootCauseId/fix-with-ai" element={<FixWithAIPage />} />
            <Route path="/root-cause/:rootCauseId/deployment-package" element={<DeploymentPackagePage />} />
            <Route path="/approval/:token" element={<ApprovalPage />} />
            <Route path="*" element={<DashboardPage />} />
          </Routes>
        </div>
      </BrowserRouter>
    </AppErrorBoundary>
  );
}