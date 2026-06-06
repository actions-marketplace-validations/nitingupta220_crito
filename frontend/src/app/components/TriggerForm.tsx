"use client";

import { useState } from "react";
import { GitPullRequest, Play, CheckCircle, AlertCircle, Loader2 } from "lucide-react";
import { api } from "../lib/api";

export default function TriggerForm() {
  const [repo, setRepo] = useState("");
  const [prNumber, setPrNumber] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repo.trim() || !prNumber.trim()) return;

    setLoading(true);
    setResult(null);

    try {
      const data = await api.triggerReview(repo.trim(), parseInt(prNumber));
      setResult({ success: true, message: data.message || "Review triggered successfully!" });
      setRepo("");
      setPrNumber("");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to trigger review";
      setResult({ success: false, message: msg });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-card p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-xl flex items-center justify-center"
          style={{ background: "linear-gradient(135deg, #7c3aed, #2563eb)" }}>
          <Play size={18} className="text-white" />
        </div>
        <div>
          <h2 className="font-semibold text-base" style={{ color: "#e6edf3" }}>Trigger PR Review</h2>
          <p className="text-xs" style={{ color: "#8b949e" }}>Manually run multi-agent analysis on any PR</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "#8b949e" }}>
              Repository (owner/repo)
            </label>
            <div className="relative">
              <GitPullRequest size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "#6e7681" }} />
              <input
                type="text"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="e.g. facebook/react"
                required
                className="w-full pl-9 pr-3 py-2.5 text-sm rounded-lg transition-all duration-200 focus:outline-none"
                style={{
                  background: "#0d1117",
                  border: "1px solid #30363d",
                  color: "#e6edf3",
                }}
                onFocus={e => (e.target.style.borderColor = "#7c3aed")}
                onBlur={e => (e.target.style.borderColor = "#30363d")}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "#8b949e" }}>
              PR Number
            </label>
            <input
              type="number"
              value={prNumber}
              onChange={(e) => setPrNumber(e.target.value)}
              placeholder="e.g. 42"
              required
              min={1}
              className="w-full px-3 py-2.5 text-sm rounded-lg transition-all duration-200 focus:outline-none"
              style={{
                background: "#0d1117",
                border: "1px solid #30363d",
                color: "#e6edf3",
              }}
              onFocus={e => (e.target.style.borderColor = "#7c3aed")}
              onBlur={e => (e.target.style.borderColor = "#30363d")}
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full py-2.5 px-4 rounded-lg font-semibold text-sm text-white transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
          style={{
            background: loading ? "#30363d" : "linear-gradient(135deg, #7c3aed, #2563eb)",
            boxShadow: loading ? "none" : "0 4px 15px rgba(124, 58, 237, 0.4)",
          }}>
          {loading ? (
            <><Loader2 size={16} className="animate-spin" /> Triggering...</>
          ) : (
            <><Play size={16} /> Run AI Review</>
          )}
        </button>
      </form>

      {result && (
        <div className={`mt-4 p-3 rounded-lg flex items-start gap-2 text-sm animate-fadeIn`}
          style={{
            background: result.success ? "#10b98118" : "#ef444418",
            border: `1px solid ${result.success ? "#10b98140" : "#ef444440"}`,
            color: result.success ? "#34d399" : "#f87171",
          }}>
          {result.success ? <CheckCircle size={16} className="mt-0.5 shrink-0" /> : <AlertCircle size={16} className="mt-0.5 shrink-0" />}
          <span>{result.message}</span>
        </div>
      )}
    </div>
  );
}
