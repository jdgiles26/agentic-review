"use client";

import { useEffect, useMemo, useState } from "react";

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
  file_path?: string;
  suggestion?: string;
  confidence?: number;
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
  cost_usd?: number;
};

type Health = {
  status: string;
  version?: string;
  agents_ready?: string[];
};

type Mode = "diff" | "github";

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export default function Home() {
  const [mode, setMode] = useState<Mode>("diff");
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
      if (!res.ok) throw new Error(await res.text());
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
        </div>
        <HealthPill health={health} error={healthError} />
      </header>
      <div className="layout">
        <section className="panel">
          <div className="tabs">
            <button type="button" aria-pressed={mode === "diff"} className={mode === "diff" ? "tab on" : "tab"} onClick={() => setMode("diff")}>Paste diff</button>
            <button type="button" aria-pressed={mode === "github"} className={mode === "github" ? "tab on" : "tab"} onClick={() => setMode("github")}>GitHub PR</button>
          </div>
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
            </div>
          )}
          <div className="actions">
            <button className="primary" onClick={runReview} disabled={!canRun}>
              {loading ? "Running agents…" : mode === "diff" ? "Run review" : "Review PR"}
            </button>
          </div>
        </section>
        <section className="panel">
          {error && <div className="banner bad" role="alert"><strong>Review failed.</strong> {error}</div>}
          {!result && !error && !loading && <EmptyState mode={mode} />}
          {loading && <p className="pulse">Agents reviewing…</p>}
          {result && (
            <>
              <div className="metrics">
                <Metric label="Status" value={result.status} />
                <Metric label="Recommendation" value={result.recommendation || "—"} emphasis />
                <Metric label="Score" value={result.score == null ? "—" : String(result.score)} />
                <Metric label="Duration" value={result.duration_ms != null ? `${result.duration_ms} ms` : "—"} />
              </div>
              <p className="lede">{result.summary}</p>
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
                        {f.confidence != null && <span className="cat">{Math.round(f.confidence * 100)}%</span>}
                        {f.file_path && <code className="path">{f.file_path}</code>}
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
      <footer className="foot">API <code>{API}/docs</code></footer>
      <style jsx>{`
        .shell { max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
        .topbar { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
        .eyebrow { margin: 0 0 4px; color: var(--muted); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; }
        h1 { margin: 0; font-size: 28px; }
        .layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 16px; }
        @media (max-width: 860px) { .layout { grid-template-columns: 1fr; } }
        .panel { background: var(--bg-elev); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; min-height: 420px; }
        .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
        .tab { background: var(--bg-soft); border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px; color: var(--muted); }
        .tab.on { color: var(--bg); background: var(--accent); border-color: var(--accent); font-weight: 600; }
        .label-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        label { display: block; font-weight: 600; margin-bottom: 8px; font-size: 13px; }
        textarea, input { width: 100%; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 12px; color: var(--text); }
        textarea { font-family: ui-monospace, monospace; font-size: 13px; min-height: 280px; resize: vertical; }
        .pr-grid { display: grid; gap: 12px; }
        .ghost { background: transparent; border: 1px solid var(--line); border-radius: 8px; padding: 4px 10px; color: var(--accent-2); font-size: 13px; }
        .actions { margin-top: 16px; }
        .primary { background: var(--accent); color: #062016; border: none; border-radius: 8px; padding: 10px 16px; font-weight: 700; }
        .pulse { color: var(--muted); }
        .banner.bad { background: #3f1d24; color: #fecaca; padding: 12px; border-radius: 8px; margin-bottom: 12px; }
        .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
        .lede { margin: 0 0 12px; }
        .findings-head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
        h2 { margin: 0; font-size: 16px; }
        .filters { display: flex; flex-wrap: wrap; gap: 6px; }
        .findings { list-style: none; padding: 0; margin: 12px 0 0; }
        .finding { border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-bottom: 8px; background: var(--bg-soft); }
        .finding-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
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
    <div title={error || health?.agents_ready?.join(", ") || "checking"} style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg-elev)", border: "1px solid var(--line)", borderRadius: 999, padding: "8px 12px", fontSize: 13 }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: error ? "var(--danger)" : ok ? "var(--ok)" : "var(--warn)" }} />
      <span>{error ? "API offline" : ok ? `API ${health?.status} · v${health?.version ?? "?"}` : "Checking API…"}</span>
    </div>
  );
}

function EmptyState({ mode }: { mode: Mode }) {
  return (
    <div style={{ color: "var(--muted)", padding: "48px 8px" }}>
      <h2 style={{ margin: "0 0 8px", color: "var(--text)" }}>No review yet</h2>
      <p style={{ margin: 0 }}>{mode === "diff" ? "Paste a unified diff or load the sample." : "Enter owner, repo, and a numeric PR number."}</p>
    </div>
  );
}
