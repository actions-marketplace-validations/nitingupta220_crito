import { FileCode, MapPin, Lightbulb } from "lucide-react";
import { Finding } from "../lib/api";

const SEVERITY_CONFIG: Record<string, { color: string; bg: string; label: string; icon: string }> = {
  critical: { color: "#ff4757", bg: "#ff475718", label: "CRITICAL", icon: "🔴" },
  high:     { color: "#ff6b35", bg: "#ff6b3518", label: "HIGH",     icon: "🟠" },
  medium:   { color: "#ffd700", bg: "#ffd70018", label: "MEDIUM",   icon: "🟡" },
  low:      { color: "#3b82f6", bg: "#3b82f618", label: "LOW",      icon: "🔵" },
  info:     { color: "#8b949e", bg: "#8b949e18", label: "INFO",     icon: "⚪" },
};

export default function FindingItem({ finding }: { finding: Finding }) {
  const cfg = SEVERITY_CONFIG[finding.severity] ?? SEVERITY_CONFIG.info;

  return (
    <div className="glass-card p-4 animate-fadeIn transition-all duration-200 hover:scale-[1.005]"
      style={{ borderLeft: `3px solid ${cfg.color}` }}>
      <div className="flex items-start gap-3">
        <span className="text-base mt-0.5">{cfg.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="font-semibold text-sm" style={{ color: "#e6edf3" }}>{finding.title}</span>
            <span className="text-xs px-2 py-0.5 rounded font-semibold"
              style={{ background: cfg.bg, color: cfg.color }}>
              {cfg.label}
            </span>
            {finding.category && (
              <span className="text-xs px-2 py-0.5 rounded" style={{ background: "#21262d", color: "#8b949e" }}>
                {finding.category.replace(/_/g, " ")}
              </span>
            )}
            <span className="text-xs" style={{ color: "#6e7681" }}>via {finding.source}</span>
          </div>

          {finding.description && (
            <p className="text-sm mb-2" style={{ color: "#8b949e" }}>{finding.description}</p>
          )}

          <div className="flex items-center gap-4 flex-wrap">
            {finding.file && (
              <div className="flex items-center gap-1 text-xs font-mono" style={{ color: "#6e7681" }}>
                <FileCode size={11} />
                <span>{finding.file}</span>
                {finding.line && <span style={{ color: "#8b949e" }}>:{finding.line}</span>}
              </div>
            )}
          </div>

          {finding.suggestion && (
            <div className="mt-2 p-2.5 rounded-lg flex gap-2 text-sm"
              style={{ background: "#10b98110", border: "1px solid #10b98130" }}>
              <Lightbulb size={14} style={{ color: "#10b981" }} className="mt-0.5 shrink-0" />
              <span style={{ color: "#6ee7b7" }}>{finding.suggestion}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
