"use client";

import Link from "next/link";
import { ExternalLink, ChevronRight, Clock, Code2, User } from "lucide-react";
import StatusBadge from "./StatusBadge";
import { ReviewSummary } from "../lib/api";

function formatTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function formatDuration(start: string | null, end: string | null) {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function ReviewsTable({ reviews, loading }: { reviews: ReviewSummary[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="glass-card overflow-hidden">
        <div className="p-4 border-b" style={{ borderColor: "#21262d" }}>
          <div className="skeleton h-5 w-32" />
        </div>
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="p-4 border-b flex gap-4" style={{ borderColor: "#21262d" }}>
            <div className="skeleton h-4 flex-1" />
            <div className="skeleton h-4 w-20" />
            <div className="skeleton h-4 w-16" />
          </div>
        ))}
      </div>
    );
  }

  if (reviews.length === 0) {
    return (
      <div className="glass-card p-12 text-center">
        <div className="w-16 h-16 rounded-2xl mx-auto mb-4 flex items-center justify-center"
          style={{ background: "#1c2128", border: "1px solid #30363d" }}>
          <Code2 size={28} style={{ color: "#6e7681" }} />
        </div>
        <h3 className="font-semibold mb-2" style={{ color: "#e6edf3" }}>No reviews yet</h3>
        <p className="text-sm" style={{ color: "#8b949e" }}>
          Trigger your first AI review or connect a GitHub webhook to get started.
        </p>
      </div>
    );
  }

  return (
    <div className="glass-card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr style={{ background: "#161b22", borderBottom: "1px solid #21262d" }}>
              {["Repository & PR", "Author", "Status", "Diff", "Time", "Duration", ""].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-xs font-semibold uppercase tracking-wider"
                  style={{ color: "#6e7681" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {reviews.map((r, idx) => {
              const duration = formatDuration(r.started_at, r.completed_at);
              return (
                <tr key={r.id}
                  className="transition-colors duration-150 hover:bg-white/[0.02]"
                  style={{
                    borderBottom: idx < reviews.length - 1 ? "1px solid #21262d" : "none",
                    animationDelay: `${idx * 50}ms`,
                  }}>
                  {/* Repo + PR */}
                  <td className="px-4 py-3.5">
                    <div className="font-medium text-sm truncate max-w-[220px]" style={{ color: "#e6edf3" }}>
                      {r.pr_title || "Untitled PR"}
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span className="text-xs" style={{ color: "#8b949e" }}>{r.repo}</span>
                      {r.pr_number && (
                        <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: "#21262d", color: "#8b949e" }}>
                          #{r.pr_number}
                        </span>
                      )}
                      {r.pr_url && (
                        <a href={r.pr_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>
                          <ExternalLink size={11} style={{ color: "#6e7681" }} className="hover:text-blue-400 transition-colors" />
                        </a>
                      )}
                    </div>
                  </td>

                  {/* Author */}
                  <td className="px-4 py-3.5">
                    {r.pr_author ? (
                      <div className="flex items-center gap-1.5 text-xs" style={{ color: "#8b949e" }}>
                        <User size={12} />
                        {r.pr_author}
                      </div>
                    ) : <span style={{ color: "#6e7681" }}>—</span>}
                  </td>

                  {/* Status */}
                  <td className="px-4 py-3.5">
                    <StatusBadge status={r.status} />
                  </td>

                  {/* Diff */}
                  <td className="px-4 py-3.5 text-xs font-mono" style={{ color: "#8b949e" }}>
                    {r.diff_size ? `${r.diff_size.toLocaleString()}` : "—"}
                  </td>

                  {/* Time */}
                  <td className="px-4 py-3.5">
                    <div className="flex items-center gap-1 text-xs" style={{ color: "#6e7681" }}>
                      <Clock size={11} />
                      {formatTime(r.started_at)}
                    </div>
                  </td>

                  {/* Duration */}
                  <td className="px-4 py-3.5 text-xs font-mono" style={{ color: "#8b949e" }}>
                    {duration ?? "—"}
                  </td>

                  {/* Action */}
                  <td className="px-4 py-3.5">
                    <Link href={`/reviews/${r.id}`}
                      className="flex items-center gap-1 text-xs font-medium transition-colors hover:text-purple-400"
                      style={{ color: "#8b949e" }}>
                      View <ChevronRight size={13} />
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
