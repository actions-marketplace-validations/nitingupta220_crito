import { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string | number;
  icon: LucideIcon;
  color: string;
  subtitle?: string;
}

export default function StatCard({ label, value, icon: Icon, color, subtitle }: StatCardProps) {
  return (
    <div className="glass-card p-6 flex items-start gap-4 animate-fadeIn transition-all duration-300 hover:scale-[1.02]"
      style={{ borderTop: `3px solid ${color}` }}>
      <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
        style={{ background: `${color}18`, border: `1px solid ${color}40` }}>
        <Icon size={22} style={{ color }} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-2xl font-bold" style={{ color: "#e6edf3" }}>{value}</div>
        <div className="text-sm font-medium mt-0.5" style={{ color: "#8b949e" }}>{label}</div>
        {subtitle && <div className="text-xs mt-1" style={{ color: "#6e7681" }}>{subtitle}</div>}
      </div>
    </div>
  );
}
