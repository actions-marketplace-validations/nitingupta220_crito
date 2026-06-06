"use client";

import { useEffect, useState, useCallback } from "react";
import { GitPullRequest, AlertTriangle, CheckCircle, Activity, RefreshCw } from "lucide-react";
import StatCard from "./components/StatCard";
import TriggerForm from "./components/TriggerForm";
import ReviewsTable from "./components/ReviewsTable";
import { api, ReviewSummary } from "./lib/api";

export default function DashboardPage() {
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const fetchReviews = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listReviews(50, 0);
      setReviews(data.reviews);
      setLastRefresh(new Date());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to connect to API");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchReviews();
    const interval = setInterval(fetchReviews, 30000);
    return () => clearInterval(interval);
  }, [fetchReviews]);

  // Stats
  const total = reviews.length;
  const completed = reviews.filter(r => r.status === "completed").length;
  const running = reviews.filter(r => r.status === "running").length;
  const failed = reviews.filter(r => r.status === "failed").length;

  return (
    <div className="p-6 space-y-8">
      {/* Hero Header */}
      <div className="relative rounded-2xl overflow-hidden p-8 hero-gradient"
        style={{ border: "1px solid #21262d" }}>
        {/* Decorative glow */}
        <div className="absolute top-0 right-0 w-96 h-96 rounded-full opacity-10 blur-3xl pointer-events-none"
          style={{ background: "radial-gradient(circle, #7c3aed, transparent)" }} />
        <div className="absolute bottom-0 left-0 w-64 h-64 rounded-full opacity-10 blur-3xl pointer-events-none"
          style={{ background: "radial-gradient(circle, #2563eb, transparent)" }} />

        <div className="relative">
          <div className="flex items-center gap-2 mb-3">
            <div className="px-3 py-1 rounded-full text-xs font-semibold flex items-center gap-1.5"
              style={{ background: "#7c3aed20", color: "#a78bfa", border: "1px solid #7c3aed40" }}>
              <div className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
              AI-Powered Code Review
            </div>
          </div>
          <h1 className="text-3xl font-bold mb-2 gradient-text">PR Review Dashboard</h1>
          <p className="text-base max-w-xl" style={{ color: "#8b949e" }}>
            Multi-agent AI system powered by 5 specialized agents — bug detection, security, performance,
            code quality, and documentation analysis.
          </p>

          <div className="flex items-center gap-4 mt-4 flex-wrap">
            <button onClick={fetchReviews}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 hover:bg-white/10"
              style={{ background: "#ffffff0a", border: "1px solid #30363d", color: "#8b949e" }}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
            <span className="text-xs" style={{ color: "#6e7681" }} suppressHydrationWarning>
              {lastRefresh ? `Last updated: ${lastRefresh.toLocaleTimeString()}` : "Loading…"}
            </span>
          </div>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="p-4 rounded-xl flex items-center gap-3 animate-fadeIn"
          style={{ background: "#ef444418", border: "1px solid #ef444440", color: "#f87171" }}>
          <AlertTriangle size={18} className="shrink-0" />
          <div>
            <div className="font-semibold text-sm">Cannot reach API</div>
            <div className="text-xs mt-0.5 opacity-80">{error} — Make sure the FastAPI server is running on port 8000.</div>
          </div>
        </div>
      )}

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Total Reviews" value={loading ? "—" : total} icon={GitPullRequest} color="#7c3aed" subtitle="All time" />
        <StatCard label="Completed" value={loading ? "—" : completed} icon={CheckCircle} color="#10b981" subtitle={`${total ? Math.round(completed / total * 100) : 0}% success rate`} />
        <StatCard label="Running Now" value={loading ? "—" : running} icon={Activity} color="#3b82f6" subtitle={running > 0 ? "Analysis in progress" : "All idle"} />
        <StatCard label="Failed" value={loading ? "—" : failed} icon={AlertTriangle} color="#ef4444" subtitle={failed > 0 ? "Needs attention" : "No failures"} />
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Trigger Form */}
        <div className="xl:col-span-1">
          <TriggerForm />

          {/* Agents Legend */}
          <div className="glass-card p-5 mt-4">
            <h3 className="font-semibold text-sm mb-4" style={{ color: "#e6edf3" }}>Agent Pipeline</h3>
            <div className="space-y-2.5">
              {[
                { name: "Bug Detection",  color: "#ff4757", desc: "Logic errors, race conditions, leaks" },
                { name: "Security",       color: "#ff6b35", desc: "Injections, secrets, auth flaws" },
                { name: "Performance",    color: "#ffd700", desc: "N+1 queries, memory issues" },
                { name: "Code Quality",   color: "#00d2ff", desc: "Complexity, dead code, style" },
                { name: "Documentation",  color: "#10b981", desc: "Missing docs, comments quality" },
              ].map((a) => (
                <div key={a.name} className="flex items-start gap-3">
                  <div className="w-2 h-2 rounded-full mt-1.5 shrink-0"
                    style={{ backgroundColor: a.color, boxShadow: `0 0 6px ${a.color}80` }} />
                  <div>
                    <div className="text-sm font-medium" style={{ color: "#e6edf3" }}>{a.name}</div>
                    <div className="text-xs" style={{ color: "#6e7681" }}>{a.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Reviews Table */}
        <div className="xl:col-span-2">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-base" style={{ color: "#e6edf3" }}>
              Recent Reviews
              {!loading && <span className="ml-2 text-sm" style={{ color: "#6e7681" }}>({total})</span>}
            </h2>
          </div>
          <ReviewsTable reviews={reviews} loading={loading} />
        </div>
      </div>
    </div>
  );
}
