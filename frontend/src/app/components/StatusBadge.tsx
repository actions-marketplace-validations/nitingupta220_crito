type Status = "pending" | "running" | "completed" | "failed" | string;

const STATUS_CONFIG: Record<string, { color: string; bg: string; dot: string; label: string }> = {
  pending:   { color: "#f59e0b", bg: "#f59e0b18", dot: "#f59e0b", label: "Pending" },
  running:   { color: "#3b82f6", bg: "#3b82f618", dot: "#3b82f6", label: "Running" },
  completed: { color: "#10b981", bg: "#10b98118", dot: "#10b981", label: "Completed" },
  failed:    { color: "#ef4444", bg: "#ef444418", dot: "#ef4444", label: "Failed" },
};

export default function StatusBadge({ status }: { status: Status }) {
  const cfg = STATUS_CONFIG[status] ?? { color: "#8b949e", bg: "#8b949e18", dot: "#8b949e", label: status };
  const isRunning = status === "running";

  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold"
      style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.color}40` }}>
      <span className={`w-1.5 h-1.5 rounded-full ${isRunning ? "animate-pulse" : ""}`}
        style={{ backgroundColor: cfg.dot }} />
      {cfg.label}
    </span>
  );
}
