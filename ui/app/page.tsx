"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SAMPLE_DIFF = `--- a/src/auth.py
+++ b/src/auth.py
@@ -12,6 +12,9 @@ def login(user, password):
-    return token
+    token = sign(user)
+    print(password)
+    return token
`;

type Finding = {
  id: string;
  category: string;
  severity: string;
  title: string;
  description: string;
  file_path?: string | null;
  line_start?: number | null;
  suggestion?: string | null;
  confidence?: number;
};

type PRAction = {
  action: string;
  body?: string | null;
  labels?: string[] | null;
};

type ReviewResult = {
  request_id: string;
  status: string;
  summary: string;
  findings: Finding[];
  recommendation?: string;
  score?: number;
  duration_ms?: number;
  agents_involved: string[];
  actions?: PRAction[];
  cost_usd?: number | null;
};

type Health = {
  status: string;
  version?: string;
  agents_ready?: string[];
  llm_configured?: boolean;
  github_configured?: boolean;
};

type Mode = "diff" | "github";

const MODES: { id: Mode; label: string }[] = [
  { id: "diff", label: "Paste diff" },
  { id: "github", label: "GitHub PR" },
];

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export default function Home() {
  const [mode, setMode] = useState<Mode>("diff");
  const tabRefs = useRef<Record<Mode, HTMLButtonElement | null>>({ diff: null, github: null });

  // ARIA tabs pattern: arrow keys / Home / End move focus and selection within the tablist.
  function onTabKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const i = MODES.findIndex((m) => m.id === mode);
    const next =
      e.key === "ArrowRight" ? (i + 1) % MODES.length
      : e.key === "ArrowLeft" ? (i - 1 + MODES.length) % MODES.length
      : e.key === "Home" ? 0
      : e.key === "End" ? MODES.length - 1
      : null;
    if (next === null) return;
    e.preventDefault();
    setMode(MODES[next].id);
    tabRefs.current[MODES[next].id]?.focus();
  }
  const [diff, setDiff] = useState("");
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [prNumber, setPrNumber] = useState("");
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<string>("all");

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      fetch(`${API}/health`)
        .then(async (res) => {
          if (!res.ok) throw new Error(await res.text());
          return res.json();
        })
        .then((data: Health) => {
          if (!cancelled) {
            setHealth(data);
            setHealthError(null);
          }
        })
        .catch((e: Error) => {
          if (!cancelled) setHealthError(e.message || "API offline");
        });
    check();
    const timer = setInterval(check, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const canRun =
    !loading &&
    (mode === "diff"
      ? Boolean(diff.trim())
      : Boolean(owner.trim() && repo.trim() && /^\d+$/.test(prNumber.trim())));

  async function runReview() {
    setLoading(true);
    setError(null);
    setResult(null);
    setSeverityFilter("all");
    try {
      const url =
        mode === "diff"
          ? `${API}/v1/review`
          : `${API}/v1/review/github/${encodeURIComponent(owner.trim())}/${encodeURIComponent(repo.trim())}/${encodeURIComponent(prNumber.trim())}`;
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: mode === "diff" ? JSON.stringify({ diff, include_suggestions: true }) : undefined,
      });
      if (!res.ok) throw new Error(await errorText(res));
      const data: ReviewResult = await res.json();
      setResult(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  const findings = result?.findings ?? [];
  const visible = useMemo(() => {
    const list =
      severityFilter === "all"
        ? findings
        : findings.filter((f) => f.severity === severityFilter);
    return [...list].sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)
    );
  }, [findings, severityFilter]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of findings) c[f.severity] = (c[f.severity] || 0) + 1;
    return c;
  }, [findings]);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Multi-agent review</p>
          <h1>Review Console</h1>
          <p className="subtitle">Paste a diff or pull a GitHub PR. Agents return findings with severity and confidence.</p>
        </div>
        <HealthPill health={health} error={healthError} />
      </header>
      <div className="layout">
        <section className="panel">
          <div className="tabs" role="tablist" aria-label="Review source" onKeyDown={onTabKeyDown}>
            {MODES.map((m) => (
              <button
                key={m.id}
                ref={(el) => {
                  tabRefs.current[m.id] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${m.id}`}
                aria-controls={`panel-${m.id}`}
                aria-selected={mode === m.id}
                tabIndex={mode === m.id ? 0 : -1}
                className={mode === m.id ? "tab on" : "tab"}
                onClick={() => setMode(m.id)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`panel-${mode}`} aria-labelledby={`tab-${mode}`}>
          {mode === "diff" ? (
            <>
              <div className="label-row">
                <label htmlFor="diff">Unified diff</label>
                <button type="button" className="ghost" onClick={() => setDiff(SAMPLE_DIFF)}>Load sample</button>
              </div>
              <textarea id="diff" value={diff} onChange={(e) => setDiff(e.target.value)} rows={16} placeholder={"--- a/src/main.py\n+++ b/src/main.py"} />
            </>
          ) : (
            <div className="pr-grid">
              <Field label="Owner" value={owner} onChange={setOwner} placeholder="jdgiles26" />
              <Field label="Repo" value={repo} onChange={setRepo} placeholder="agentic-review" />
              <Field label="PR #" value={prNumber} onChange={setPrNumber} placeholder="42" inputMode="numeric" />
              <p className="hint">
                Uses <code>POST /v1/review/github/{"{owner}/{repo}/{number}"}</code>. Requires <code>GITHUB_TOKEN</code> on the API
                {health ? (health.github_configured ? " (configured)." : " (not configured on this API).") : "."}
              </p>
            </div>
          )}
          <div className="actions">
            <button className="primary" onClick={runReview} disabled={!canRun}>
              {loading ? "Running agents…" : mode === "diff" ? "Run review" : "Fetch PR and review"}
            </button>
            {loading && <span className="pulse">Supervisor → specialists → merge findings</span>}
          </div>
          </div>
        </section>
        <section className="panel">
          {error && <div className="banner bad" role="alert"><strong>Review failed.</strong> {error}</div>}
          {!result && !error && !loading && <EmptyState mode={mode} />}
          {loading && <p className="pulse loading">Agents reviewing… findings will group by severity when the graph finishes.</p>}
          {result && (
            <>
              <div className="metrics">
                <Metric label="Status" value={result.status} />
                <Metric label="Recommendation" value={result.recommendation || "—"} emphasis />
                <Metric label="Score" value={result.score == null ? "—" : String(result.score)} />
                <Metric label="Duration" value={result.duration_ms != null ? `${result.duration_ms} ms` : "—"} />
              </div>
              {result.status === "partial" && (
                <div className="banner warn" role="status">Some specialists failed; findings may be incomplete.</div>
              )}
              <p className="lede">{result.summary}</p>
              {result.agents_involved.length > 0 && (
                <div className="chips" role="group" aria-label="Agents involved">
                  {result.agents_involved.map((a) => (
                    <span key={a} className="chip">{a}</span>
                  ))}
                </div>
              )}
              {(result.actions ?? []).length > 0 && (
                <ul className="actions-list">
                  {(result.actions ?? []).map((a, i) => (
                    <li key={i}>
                      <span className="cat">{a.action}</span> {a.labels?.length ? <code className="path">{a.labels.join(", ")}</code> : null} {a.body}
                    </li>
                  ))}
                </ul>
              )}
              <div className="findings-head">
                <h2>Findings ({visible.length})</h2>
                <div className="filters">
                  <FilterChip label="all" count={findings.length} active={severityFilter === "all"} onClick={() => setSeverityFilter("all")} />
                  {SEVERITY_ORDER.filter((s) => counts[s]).map((s) => (
                    <FilterChip key={s} label={s} count={counts[s]} active={severityFilter === s} onClick={() => setSeverityFilter(s)} />
                  ))}
                </div>
              </div>
              {visible.length === 0 ? <p className="ok-empty">No findings in this filter.</p> : (
                <ul className="findings">
                  {visible.map((f) => (
                    <li key={f.id} className="finding">
                      <div className="finding-meta">
                        <span className={`sev sev-${f.severity}`}>{f.severity}</span>
                        <span className="cat">{f.category}</span>
                        {f.file_path && <code className="path">{f.file_path}{f.line_start ? `:${f.line_start}` : ""}</code>}
                        {f.confidence != null && <span className="conf">{Math.round(f.confidence * 100)}% confidence</span>}
                      </div>
                      <strong>{f.title}</strong>
                      <p>{f.description}</p>
                      {f.suggestion && <pre>{f.suggestion}</pre>}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      </div>
      <footer className="foot">API <code>{API}/docs</code> · CLI <code>review diff file.patch</code></footer>
      <style jsx>{`
        .shell { max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
        .topbar { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; margin-bottom: 24px; }
        .eyebrow { margin: 0 0 4px; color: var(--muted); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; }
        h1 { margin: 0; font-size: 28px; letter-spacing: -0.03em; }
        .subtitle { margin: 4px 0 0; color: var(--muted); }
        .layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 16px; }
        @media (max-width: 860px) { .layout { grid-template-columns: 1fr; } }
        .panel { background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; min-height: 420px; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab { background: var(--bg-soft); border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px; color: var(--muted); }
        .tab.on { color: var(--bg); background: var(--accent); border-color: var(--accent); font-weight: 600; }
        .label-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        textarea { font-family: ui-monospace, monospace; font-size: 13px; min-height: 280px; resize: vertical; }
        .pr-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 96px); gap: 12px; }
        @media (max-width: 600px) { .pr-grid { grid-template-columns: 1fr; } }
        .hint { grid-column: 1 / -1; color: var(--muted); font-size: 13px; margin: 0; overflow-wrap: anywhere; }
        .ghost { background: transparent; border: 1px solid var(--line); border-radius: 8px; padding: 4px 10px; color: var(--accent-2); font-size: 13px; }
        .actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 16px; }
        .primary { background: var(--accent); color: #062016; border: none; border-radius: 8px; padding: 10px 16px; font-weight: 700; }
        .pulse { color: var(--muted); font-size: 13px; }
        .pulse.loading { font-size: 14px; padding: 32px 8px; margin: 0; }
        .banner.bad { background: #3f1d24; color: #fecaca; padding: 12px; border-radius: 8px; margin-bottom: 12px; }
        .banner.warn { background: #3a2f12; color: #fde68a; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
        .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; }
        .chip { font-size: 12px; background: var(--bg-soft); border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); }
        .actions-list { list-style: none; padding: 0; margin: 0 0 12px; font-size: 13px; }
        .actions-list li { padding: 6px 0; border-top: 1px dashed var(--line); }
        .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
        @media (max-width: 700px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
        .lede { margin: 0 0 12px; }
        .findings-head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
        h2 { margin: 0; font-size: 16px; }
        .filters { display: flex; flex-wrap: wrap; gap: 6px; }
        .findings { list-style: none; padding: 0; margin: 12px 0 0; }
        .finding { border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-bottom: 8px; background: var(--bg-soft); }
        .finding-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 6px; }
        .finding p { margin: 6px 0 0; font-size: 14px; color: #d5dbe6; }
        .finding pre { background: var(--bg); padding: 8px; border-radius: 6px; overflow: auto; white-space: pre-wrap; font-size: 12px; border: 1px solid var(--line); }
        .conf { margin-left: auto; font-size: 12px; color: var(--accent-2); }
        .sev { font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 2px 6px; border-radius: 4px; color: #0b0d12; }
        .sev-critical { background: #f87171; } .sev-high { background: #fb923c; } .sev-medium { background: #fbbf24; } .sev-low { background: #7dd3fc; } .sev-info { background: #94a3b8; }
        .cat { font-size: 12px; color: var(--muted); }
        .path { font-size: 12px; color: var(--accent-2); }
        .ok-empty { color: var(--ok); }
        .foot { margin-top: 28px; color: var(--muted); font-size: 13px; }
      `}</style>
    </main>
  );
}

function Field({ label, value, onChange, placeholder, inputMode }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; inputMode?: React.HTMLAttributes<HTMLInputElement>["inputMode"]; }) {
  const id = label.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <input id={id} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} inputMode={inputMode} />
    </div>
  );
}

function Metric({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div style={{ background: "var(--bg-soft)", border: "1px solid var(--line)", borderRadius: 8, padding: 10 }}>
      <div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontWeight: emphasis ? 700 : 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}

function FilterChip({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} style={{ borderRadius: 999, border: "1px solid var(--line)", background: active ? "var(--accent)" : "var(--bg)", color: active ? "#062016" : "var(--muted)", padding: "4px 10px", fontSize: 12, fontWeight: 600 }}>
      {label} {count}
    </button>
  );
}

function HealthPill({ health, error }: { health: Health | null; error: string | null }) {
  const ok = Boolean(health && !error);
  return (
    <div title={error || health?.agents_ready?.join(", ") || "checking"} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 8px", background: "var(--bg-elev)", border: "1px solid var(--line)", borderRadius: 12, padding: "8px 12px", fontSize: 13, maxWidth: "100%" }}>
      <span style={{ width: 8, height: 8, flex: "none", borderRadius: "50%", background: error ? "var(--danger)" : ok ? "var(--ok)" : "var(--warn)" }} />
      <span>{error ? "API offline" : ok ? `API ${health?.status} · v${health?.version ?? "?"}` : "Checking API…"}</span>
      {ok && (
        <span style={{ color: "var(--muted)" }}>
          {health?.llm_configured ? "LLM" : "offline heuristics"}
          {health?.github_configured ? " · GitHub" : ""}
          {health?.agents_ready?.length ? ` · ${health.agents_ready.length} agents ready` : ""}
        </span>
      )}
    </div>
  );
}

async function errorText(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body = JSON.parse(text);
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    // not JSON; fall through to raw text
  }
  return text || `HTTP ${res.status}`;
}

function EmptyState({ mode }: { mode: Mode }) {
  return (
    <div style={{ color: "var(--muted)", padding: "48px 8px" }}>
      <h2 style={{ margin: "0 0 8px", color: "var(--text)" }}>No review yet</h2>
      <p style={{ margin: 0 }}>
        {mode === "diff"
          ? "Paste a unified diff (or load the sample) and run the specialist pipeline. Findings land here with severity first."
          : "Enter owner, repo, and a numeric PR number. The API fetches the diff and runs the same agents."}
      </p>
    </div>
  );
}
