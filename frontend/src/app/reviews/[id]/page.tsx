"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { ArrowLeft, RefreshCw, ExternalLink, User, GitBranch, Calendar, Clock, AlertTriangle, BookOpen, Bug, Shield, Zap, CheckCircle } from "lucide-react";
import StatusBadge from "../../components/StatusBadge";
import AgentCard from "../../components/AgentCard";
import FindingItem from "../../components/FindingItem";
import { api, ReviewDetail } from "../../lib/api";

function formatTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function formatDuration(start: string | null, end: string | null) {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

export default function ReviewDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const [review, setReview] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [id, setId] = useState<string>("");
  const [filterSeverity, setFilterSeverity] = useState<string>("all");

  useEffect(() => {
    params.then(p => setId(p.id));
  }, [params]);

  const fetchReview = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.getReview(id);
      setReview(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load review");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchReview();
    const interval = setInterval(() => {
      if (review?.status === "running") fetchReview();
    }, 5000);
    return () => clearInterval(interval);
  }, [fetchReview, review?.status]);

  const filteredFindings = review?.findings.filter(
    f => filterSeverity === "all" || f.severity === filterSeverity
  ) ?? [];

  const severityCounts = review?.findings.reduce((acc, f) => {
    acc[f.severity] = (acc[f.severity] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>) ?? {};

  const SEVERITY_COLORS: Record<string, string> = {
    critical: "#ff4757", high: "#ff6b35", medium: "#ffd700", low: "#3b82f6", info: "#8b949e",
  };

  if (loading) {
    return (
      <div className="p-6 space-y-4">
        <div className="skeleton h-8 w-48 mb-6" />
        <div className="skeleton h-32 rounded-xl" />
        <div className="skeleton h-48 rounded-xl" />
        <div className="skeleton h-48 rounded-xl" />
      </div>
    );
  }

  if (error || !review) {
    return (
      <div className="p-6">
        <Link href="/" className="flex items-center gap-2 text-sm mb-6 hover:text-purple-400 transition-colors" style={{ color: "#8b949e" }}>
          <ArrowLeft size={16} /> Back to Dashboard
        </Link>
        <div className="glass-card p-12 text-center">
          <AlertTriangle size={40} className="mx-auto mb-4" style={{ color: "#ef4444" }} />
          <h2 className="font-semibold text-lg mb-2" style={{ color: "#e6edf3" }}>Review Not Found</h2>
          <p className="text-sm" style={{ color: "#8b949e" }}>{error}</p>
        </div>
      </div>
    );
  }

  const duration = formatDuration(review.started_at, review.completed_at);

  return (
    <div className="p-6 space-y-6">
      {/* Top Nav */}
      <div className="flex items-center justify-between">
        <Link href="/" className="flex items-center gap-2 text-sm hover:text-purple-400 transition-colors" style={{ color: "#8b949e" }}>
          <ArrowLeft size={16} /> Back to Dashboard
        </Link>
        <button onClick={fetchReview}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all hover:bg-white/10"
          style={{ background: "#ffffff0a", border: "1px solid #30363d", color: "#8b949e" }}>
          <RefreshCw size={13} className={review.status === "running" ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* PR Metadata Card */}
      <div className="glass-card p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1 flex-wrap">
              <StatusBadge status={review.status} />
              {review.pull_request?.pr_number && (
                <span className="text-xs px-2 py-0.5 rounded font-mono"
                  style={{ background: "#21262d", color: "#8b949e" }}>
                  #{review.pull_request.pr_number}
                </span>
              )}
            </div>
            <h1 className="text-xl font-bold mt-2 mb-1" style={{ color: "#e6edf3" }}>
              {review.pull_request?.title || "PR Review"}
            </h1>
            <div className="flex items-center gap-4 flex-wrap text-sm" style={{ color: "#8b949e" }}>
              {review.pull_request?.repo && (
                <span className="flex items-center gap-1">
                  <GitBranch size={13} /> {review.pull_request.repo}
                </span>
              )}
              {review.pull_request?.author && (
                <span className="flex items-center gap-1">
                  <User size={13} /> {review.pull_request.author}
                </span>
              )}
              {review.pull_request?.url && (
                <a href={review.pull_request.url} target="_blank" rel="noreferrer"
                  className="flex items-center gap-1 hover:text-purple-400 transition-colors">
                  <ExternalLink size={13} /> View on GitHub
                </a>
              )}
            </div>
          </div>

          {/* Timing */}
          <div className="text-right space-y-1">
            <div className="flex items-center gap-2 justify-end text-xs" style={{ color: "#6e7681" }}>
              <Calendar size={11} />
              <span>Started: {formatTime(review.started_at)}</span>
            </div>
            {review.completed_at && (
              <div className="flex items-center gap-2 justify-end text-xs" style={{ color: "#6e7681" }}>
                <Clock size={11} />
                <span>Duration: {duration}</span>
              </div>
            )}
            {review.diff_size && (
              <div className="text-xs font-mono" style={{ color: "#6e7681" }}>
                {review.diff_size.toLocaleString()} diff chars
              </div>
            )}
          </div>
        </div>

        {/* Timeline bar */}
        {review.started_at && review.completed_at && (
          <div className="mt-4 pt-4 border-t" style={{ borderColor: "#21262d" }}>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full" style={{ background: "#3b82f6" }} />
              <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "#21262d" }}>
                <div className="h-full rounded-full animate-gradient"
                  style={{ width: "100%", background: "linear-gradient(90deg, #7c3aed, #2563eb, #10b981)" }} />
              </div>
              <div className="w-2 h-2 rounded-full" style={{ background: "#10b981" }} />
            </div>
          </div>
        )}
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { icon: Bug,      label: "Agents Run",  value: review.agent_outputs.length, color: "#7c3aed" },
          { icon: AlertTriangle, label: "Findings",    value: review.findings.length, color: "#ff4757" },
          { icon: Shield,   label: "Critical",    value: severityCounts["critical"] ?? 0, color: "#ff4757" },
          { icon: CheckCircle, label: "Low/Info",  value: (severityCounts["low"] ?? 0) + (severityCounts["info"] ?? 0), color: "#10b981" },
        ].map((s) => {
          const Icon = s.icon;
          return (
            <div key={s.label} className="glass-card p-4 text-center animate-fadeIn">
              <div className="w-8 h-8 rounded-lg mx-auto mb-2 flex items-center justify-center"
                style={{ background: `${s.color}18` }}>
                <Icon size={16} style={{ color: s.color }} />
              </div>
              <div className="text-2xl font-bold" style={{ color: "#e6edf3" }}>{s.value}</div>
              <div className="text-xs mt-0.5" style={{ color: "#6e7681" }}>{s.label}</div>
            </div>
          );
        })}
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Agent Outputs */}
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Zap size={16} style={{ color: "#7c3aed" }} />
            <h2 className="font-semibold" style={{ color: "#e6edf3" }}>
              Agent Outputs <span style={{ color: "#6e7681" }}>({review.agent_outputs.length})</span>
            </h2>
          </div>
          <div className="space-y-3">
            {review.agent_outputs.length === 0 ? (
              <div className="glass-card p-8 text-center text-sm" style={{ color: "#6e7681" }}>
                No agent outputs yet.
              </div>
            ) : (
              review.agent_outputs.map((ao) => (
                <AgentCard key={ao.agent} output={ao} />
              ))
            )}
          </div>
        </div>

        {/* Findings */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <BookOpen size={16} style={{ color: "#ff4757" }} />
              <h2 className="font-semibold" style={{ color: "#e6edf3" }}>
                Findings <span style={{ color: "#6e7681" }}>({filteredFindings.length})</span>
              </h2>
            </div>

            {/* Severity filter */}
            <div className="flex gap-1 flex-wrap">
              <button onClick={() => setFilterSeverity("all")}
                className="px-2 py-1 rounded text-xs font-medium transition-all"
                style={{
                  background: filterSeverity === "all" ? "#7c3aed" : "#21262d",
                  color: filterSeverity === "all" ? "white" : "#8b949e",
                }}>
                All
              </button>
              {SEVERITY_ORDER.filter(s => severityCounts[s]).map(s => (
                <button key={s} onClick={() => setFilterSeverity(s)}
                  className="px-2 py-1 rounded text-xs font-medium transition-all capitalize"
                  style={{
                    background: filterSeverity === s ? SEVERITY_COLORS[s] : "#21262d",
                    color: filterSeverity === s ? "white" : "#8b949e",
                  }}>
                  {s} ({severityCounts[s]})
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 max-h-[700px] overflow-y-auto pr-1">
            {filteredFindings.length === 0 ? (
              <div className="glass-card p-8 text-center">
                <CheckCircle size={32} className="mx-auto mb-2" style={{ color: "#10b981" }} />
                <p className="text-sm" style={{ color: "#6e7681" }}>
                  {review.findings.length === 0 ? "No findings — great code!" : "No findings for this filter."}
                </p>
              </div>
            ) : (
              filteredFindings
                .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity))
                .map((f) => (
                  <FindingItem key={f.id} finding={f} />
                ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
