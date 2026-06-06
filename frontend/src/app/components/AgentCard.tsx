"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Clock, Cpu, Hash } from "lucide-react";
import { AgentOutput } from "../lib/api";

const AGENT_META: Record<string, { label: string; colorClass: string; color: string; emoji: string }> = {
  bug_detection:  { label: "Bug Detection",  colorClass: "agent-bug",         color: "#ff4757", emoji: "🐛" },
  security:       { label: "Security",        colorClass: "agent-security",     color: "#ff6b35", emoji: "🛡️" },
  performance:    { label: "Performance",     colorClass: "agent-performance",  color: "#ffd700", emoji: "⚡" },
  quality:        { label: "Code Quality",    colorClass: "agent-quality",      color: "#00d2ff", emoji: "✨" },
  documentation:  { label: "Documentation",   colorClass: "agent-docs",         color: "#10b981", emoji: "📚" },
  aggregator:     { label: "Aggregator",      colorClass: "agent-aggregator",   color: "#a78bfa", emoji: "🤖" },
};

const RISK_COLORS: Record<string, string> = {
  critical: "#ff4757", high: "#ff6b35", medium: "#ffd700",
  low: "#3b82f6", none: "#10b981", info: "#8b949e",
};

/**
 * The FastAPI backend stores raw_output as a JSON column and returns it already
 * deserialized — so `output.output` arrives as a JS object, NOT a string.
 * This helper normalises both cases (object or JSON string) into a plain object.
 */
function toObject(raw: unknown): Record<string, unknown> | null {
  if (!raw) return null;
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try { return JSON.parse(raw); } catch { return null; }
  }
  return null;
}

/** Safely convert anything into a string safe for React children / <pre> */
function safeStr(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

type Finding = Record<string, unknown>;

export default function AgentCard({ output }: { output: AgentOutput }) {
  const [expanded, setExpanded] = useState(false);

  const meta = AGENT_META[output.agent] ?? {
    label: output.agent,
    colorClass: "",
    color: "#8b949e",
    emoji: "🤖",
  };

  const parsed = toObject(output.output);
  const riskLevel = safeStr(parsed?.risk_level ?? parsed?.overall_risk ?? null) || null;
  const summary = typeof parsed?.summary === "string" ? parsed.summary : null;
  const findings: Finding[] = Array.isArray(parsed?.findings)
    ? (parsed.findings as Finding[])
    : [];
  const positiveObs: string[] = Array.isArray(parsed?.positive_observations)
    ? (parsed.positive_observations as string[])
    : [];

  return (
    <div className={`glass-card overflow-hidden animate-fadeIn ${meta.colorClass}`}>
      {/* Header / toggle button */}
      <button
        className="w-full p-4 flex items-center gap-3 text-left transition-colors hover:bg-white/[0.02]"
        onClick={() => setExpanded(!expanded)}>
        <span className="text-xl">{meta.emoji}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm" style={{ color: "#e6edf3" }}>{meta.label}</span>
            {riskLevel && (
              <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
                style={{
                  background: `${RISK_COLORS[riskLevel] ?? "#8b949e"}20`,
                  color: RISK_COLORS[riskLevel] ?? "#8b949e",
                  border: `1px solid ${RISK_COLORS[riskLevel] ?? "#8b949e"}40`,
                }}>
                {riskLevel.toUpperCase()} RISK
              </span>
            )}
            {findings.length > 0 && (
              <span className="text-xs px-2 py-0.5 rounded"
                style={{ background: "#21262d", color: "#6e7681" }}>
                {findings.length} finding{findings.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            {output.model && (
              <span className="flex items-center gap-1 text-xs" style={{ color: "#6e7681" }}>
                <Cpu size={10} />{output.model.split("/").pop()}
              </span>
            )}
            {output.tokens && (
              <span className="flex items-center gap-1 text-xs" style={{ color: "#6e7681" }}>
                <Hash size={10} />{output.tokens.toLocaleString()} tokens
              </span>
            )}
            {output.latency_ms && (
              <span className="flex items-center gap-1 text-xs" style={{ color: "#6e7681" }}>
                <Clock size={10} />{(output.latency_ms / 1000).toFixed(2)}s
              </span>
            )}
          </div>
        </div>
        {expanded
          ? <ChevronUp size={16} style={{ color: "#8b949e" }} />
          : <ChevronDown size={16} style={{ color: "#8b949e" }} />
        }
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t px-4 py-4 space-y-3" style={{ borderColor: "#21262d" }}>
          {parsed ? (
            <>
              {/* Summary sentence */}
              {summary && (
                <div className="text-sm p-3 rounded-lg" style={{ background: "#161b22", color: "#e6edf3" }}>
                  {summary}
                </div>
              )}

              {/* Findings list */}
              {findings.length > 0 && (
                <div className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wider mb-1"
                    style={{ color: "#6e7681" }}>
                    {findings.length} Finding{findings.length !== 1 ? "s" : ""}
                  </div>
                  {findings.map((f, i) => {
                    const sev = safeStr(f.severity) || "info";
                    return (
                      <div key={i} className="p-3 rounded-lg text-xs space-y-1.5"
                        style={{
                          background: "#0d1117",
                          border: `1px solid ${RISK_COLORS[sev] ?? "#30363d"}40`,
                        }}>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-bold uppercase px-1.5 py-0.5 rounded"
                            style={{
                              background: `${RISK_COLORS[sev] ?? "#8b949e"}25`,
                              color: RISK_COLORS[sev] ?? "#8b949e",
                            }}>
                            {sev}
                          </span>
                          {f.category && (
                            <span className="px-1.5 py-0.5 rounded"
                              style={{ background: "#21262d", color: "#8b949e" }}>
                              {safeStr(f.category).replace(/_/g, " ")}
                            </span>
                          )}
                          <span className="font-semibold" style={{ color: "#e6edf3" }}>
                            {safeStr(f.title || f.description)}
                          </span>
                        </div>
                        {f.description && f.title && (
                          <p style={{ color: "#8b949e" }}>{safeStr(f.description)}</p>
                        )}
                        {(f.file || f.file_path || f.line_hint) && (
                          <p className="font-mono" style={{ color: "#6e7681" }}>
                            📁 {safeStr(f.file ?? f.file_path)}{f.line_hint ? ` → ${safeStr(f.line_hint)}` : ""}
                          </p>
                        )}
                        {f.suggestion && (
                          <div className="p-2 rounded" style={{ background: "#10b98110", color: "#6ee7b7" }}>
                            💡 {safeStr(f.suggestion)}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Positive observations */}
              {positiveObs.length > 0 && (
                <div className="space-y-1">
                  <div className="text-xs font-semibold uppercase tracking-wider"
                    style={{ color: "#10b981" }}>
                    ✅ Positive Observations
                  </div>
                  {positiveObs.map((obs, i) => (
                    <div key={i} className="text-xs p-2 rounded"
                      style={{ background: "#10b98110", color: "#6ee7b7" }}>
                      {obs}
                    </div>
                  ))}
                </div>
              )}

              {/* If nothing structured — dump pretty JSON */}
              {!summary && findings.length === 0 && positiveObs.length === 0 && (
                <pre className="text-xs overflow-auto max-h-64 p-3 rounded-lg whitespace-pre-wrap"
                  style={{ background: "#161b22", color: "#8b949e", fontFamily: "monospace" }}>
                  {safeStr(output.output)}
                </pre>
              )}
            </>
          ) : (
            /* Not JSON at all — render safely as a string */
            <pre className="text-xs overflow-auto max-h-64 p-3 rounded-lg whitespace-pre-wrap"
              style={{ background: "#161b22", color: "#8b949e", fontFamily: "monospace" }}>
              {safeStr(output.output)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
