"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, GitPullRequest, Activity, Zap, Shield, Bug, FileText } from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/reviews", label: "Reviews", icon: GitPullRequest },
];

const agentItems = [
  { icon: Bug, label: "Bug Detection", color: "#ff4757" },
  { icon: Shield, label: "Security", color: "#ff6b35" },
  { icon: Zap, label: "Performance", color: "#ffd700" },
  { icon: Activity, label: "Quality", color: "#00d2ff" },
  { icon: FileText, label: "Docs", color: "#10b981" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 shrink-0 h-screen sticky top-0 flex flex-col" style={{ background: "#0d1117", borderRight: "1px solid #21262d" }}>
      {/* Logo */}
      <div className="p-6 flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center animate-pulse-glow"
          style={{ background: "linear-gradient(135deg, #7c3aed, #2563eb)" }}>
          <GitPullRequest size={18} className="text-white" />
        </div>
        <div>
          <div className="font-bold text-sm" style={{ color: "#e6edf3" }}>PR Review AI</div>
          <div className="text-xs" style={{ color: "#8b949e" }}>Multi-Agent System</div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-2 space-y-1">
        <div className="text-xs font-semibold uppercase tracking-wider mb-3 px-3" style={{ color: "#6e7681" }}>
          Navigation
        </div>
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
          return (
            <Link key={item.href} href={item.href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${isActive ? "nav-active" : "hover:bg-white/5"}`}
              style={{ color: isActive ? "#a78bfa" : "#8b949e" }}>
              <Icon size={16} />
              {item.label}
            </Link>
          );
        })}

        {/* Agents section */}
        <div className="text-xs font-semibold uppercase tracking-wider mb-3 mt-6 px-3" style={{ color: "#6e7681" }}>
          Active Agents
        </div>
        {agentItems.map((agent) => {
          const Icon = agent.icon;
          return (
            <div key={agent.label} className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm" style={{ color: "#8b949e" }}>
              <div className="w-2 h-2 rounded-full" style={{ backgroundColor: agent.color, boxShadow: `0 0 6px ${agent.color}` }} />
              <Icon size={14} />
              <span className="text-xs">{agent.label}</span>
            </div>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="p-4 m-3 rounded-xl" style={{ background: "#161b22", border: "1px solid #21262d" }}>
        <div className="flex items-center gap-2 mb-1">
          <div className="w-2 h-2 rounded-full bg-emerald-400" style={{ boxShadow: "0 0 6px #10b981" }} />
          <span className="text-xs font-medium" style={{ color: "#10b981" }}>System Online</span>
        </div>
        <div className="text-xs" style={{ color: "#6e7681" }}>API: localhost:8000</div>
      </div>
    </aside>
  );
}
