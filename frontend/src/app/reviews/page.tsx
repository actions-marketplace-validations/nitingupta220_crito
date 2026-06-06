"use client";

import { useEffect, useState, useCallback } from "react";
import { GitPullRequest, RefreshCw, AlertTriangle } from "lucide-react";
import ReviewsTable from "../components/ReviewsTable";
import { api, ReviewSummary } from "../lib/api";

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<ReviewSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 20;

  const fetchReviews = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listReviews(PAGE_SIZE, page * PAGE_SIZE);
      setReviews(data.reviews);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { fetchReviews(); }, [fetchReviews]);

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #7c3aed, #2563eb)" }}>
            <GitPullRequest size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold" style={{ color: "#e6edf3" }}>All Reviews</h1>
            <p className="text-xs" style={{ color: "#8b949e" }}>Full history of AI code reviews</p>
          </div>
        </div>
        <button onClick={fetchReviews}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm transition-all hover:bg-white/10"
          style={{ background: "#ffffff0a", border: "1px solid #30363d", color: "#8b949e" }}>
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl flex items-center gap-3"
          style={{ background: "#ef444418", border: "1px solid #ef444440", color: "#f87171" }}>
          <AlertTriangle size={16} />
          <span className="text-sm">{error}</span>
        </div>
      )}

      <ReviewsTable reviews={reviews} loading={loading} />

      {/* Pagination */}
      {!loading && reviews.length === PAGE_SIZE && (
        <div className="flex justify-center gap-3">
          <button
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-all disabled:opacity-40"
            style={{ background: "#21262d", color: "#8b949e", border: "1px solid #30363d" }}>
            Previous
          </button>
          <span className="flex items-center text-sm" style={{ color: "#6e7681" }}>Page {page + 1}</span>
          <button
            onClick={() => setPage(p => p + 1)}
            className="px-4 py-2 rounded-lg text-sm font-medium transition-all"
            style={{ background: "#21262d", color: "#8b949e", border: "1px solid #30363d" }}>
            Next
          </button>
        </div>
      )}
    </div>
  );
}
