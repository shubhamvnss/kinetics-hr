# Corporate Loan — KPI dashboard (React)

Two sections, fed entirely by the JSON the Python pipeline already writes:

1. **Tier 1 KPI tiles** — one tile per headline KPI. The current value is the dominant element;
   target and variance sit beneath it, with a health badge and a trend marker vs the previous run.
   Critical tiles are buttons: clicking one jumps to that KPI's root causes.
2. **Root causes** — for every Critical Tier 1 KPI: its performance summary, why it is not
   performing, and the ranked root causes with statistics, gap references, linked Tier 2 KPIs and
   affected-case counts. Each KPI block ends with a **Fix with AI** button (see below).

Nothing is recomputed in the browser. The agent's `frontend_summary` is a presentation-ready layer
by contract (prompt section 23), so the UI renders it as-is — it never re-ranks or re-aggregates.

## Run it

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

With no data files present it renders **clearly-labelled sample data** so the layout can be
reviewed offline. An amber banner stays on screen for as long as sample data is in use.

## Point it at a real KPI run

The dashboard reads two files from `public/data/`:

| File | Produced by | Used for |
|---|---|---|
| `public/data/evidence.json` | `root_cause_agent.py --evidence-out` | the KPI tiles (`tier1_all`) and the run header (`run_stats`) |
| `public/data/root_causes.json` | `root_cause_agent.py --out` | the root-cause section (`frontend_summary`) |

```bash
cd ..
mkdir -p frontend/public/data
python root_cause_agent.py \
    --out          frontend/public/data/root_causes.json \
    --evidence-out frontend/public/data/evidence.json
```

Reload the page — the sample banner disappears. Either file may be missing: without
`evidence.json` the tiles fall back to the Critical KPIs named in the analysis; without
`root_causes.json` the tiles render and the root-cause section explains how to generate it.

To refresh the tiles without paying for a model run, write just the evidence package:

```bash
python root_cause_tool.py --out frontend/public/data/evidence.json
```

## "Fix with AI"

Every root cause carries a *Fix with AI* button. It routes to

    /root-cause/:rootCauseId/fix-with-ai        e.g. /root-cause/RC-T1-01-1/fix-with-ai

The id is derived from the KPI id and the cause's rank (`rootCauseId()` in `src/api/client.js`),
exactly as the backend derives it, so no lookup round trip is needed.

That screen calls the Fix-with-AI service and renders four sections:

1. **AI Fix Opportunity** - the root cause, its KPI context, and how many cases it impacts
2. **Most Representative Case** - one real case, its timeline with the root-cause evidence
   highlighted, and why it represents the cause
3. **Recommended AI Fix Plan** - the agents as one coordinated sequence, each with purpose,
   problem addressed, impact, action, trigger, systems and its human-in-the-loop position
4. **Target Process** - the future-state flow

**The backend must be running** for this route (`uvicorn backend.main:app --port 8000` from the
project folder); the KPI tiles and root causes still work without it. Point the frontend elsewhere
with `VITE_API_BASE_URL`.

Every number on the screen is calculated by the backend from the event log. The model supplies the
narrative only - nothing rendered here is a figure an LLM produced.

## Build

```bash
npm run build          # -> dist/, relative asset paths, serve from anywhere
npm run preview
```

## Files

| File | Contents |
|---|---|
| `src/KpiDashboard.jsx` | The dashboard: tiles, filters, root-cause sections and cards |
| `src/pages/FixWithAI.jsx` | The AI Fix Recommendation screen |
| `src/api/client.js` | Centralised API calls and the root-cause id derivation |
| `src/App.jsx` | Routing, plus loading the two JSON files with a sample-data fallback |
| `src/format.js` | Shared number / variance / trend formatting |
| `src/dashboard.css` | Design tokens and dashboard styling - the theme for every screen |
| `src/fixwithai.css` | AI Fix Recommendation styling, using the same tokens |
| `src/sample/*.sample.json` | Placeholder data for offline review — **not** a real KPI run |

## Theme

The application is **dark by default on every screen**, unconditionally - it does not follow the
viewer's OS setting, so it looks the same on every machine. All colours come from the token block
at the top of `src/dashboard.css`; `src/fixwithai.css` reuses those tokens, so there is one palette
for the whole app.

To go back to light, add `data-theme="light"` to `<html>` in `index.html` (the light palette is
kept in `dashboard.css` under `:root[data-theme="light"]`), or move those tokens into `:root` to
make light the default again.

## Two conventions worth knowing before you edit

- **`variance_vs_target_pct` is positive when a KPI is *worse* than target**, whichever direction
  the KPI points (`kpi_core.variance_pct`). The UI never shows a bare signed number, because
  "+26.7%" reads as "the value went up" — it always renders "26.7% worse than target".
- **`trend_direction` means improving/worsening, not up/down in value** (set in
  `04_compute_kpis_to_postgres.py`). `▲` is green because the KPI improved, even when the
  underlying number fell.
