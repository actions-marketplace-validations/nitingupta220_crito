const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface ReviewSummary {
  id: string;
  status: "pending" | "running" | "completed" | "failed";
  repo: string | null;
  pr_number: number | null;
  pr_title: string | null;
  pr_author: string | null;
  pr_url: string | null;
  diff_size: number | null;
  github_comment_id: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface AgentOutput {
  agent: string;
  model: string | null;
  tokens: number | null;
  latency_ms: number | null;
  output: unknown;   // FastAPI JSON column arrives as a pre-parsed object, not a string
}

export interface Finding {
  id: string;
  source: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  category: string;
  title: string;
  description: string | null;
  file: string | null;
  line: number | null;
  suggestion: string | null;
}

export interface ReviewDetail extends ReviewSummary {
  pull_request: {
    repo: string;
    pr_number: number;
    title: string;
    author: string;
    url: string;
  } | null;
  agent_outputs: AgentOutput[];
  findings: Finding[];
}

export interface ListReviewsResponse {
  total: number;
  reviews: ReviewSummary[];
}

async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

export const api = {
  health: () => fetchApi<{ status: string }>("/health"),

  listReviews: (limit = 20, offset = 0) =>
    fetchApi<ListReviewsResponse>(`/reviews/?limit=${limit}&offset=${offset}`),

  getReview: (id: string) => fetchApi<ReviewDetail>(`/reviews/${id}`),

  triggerReview: (repo: string, pr_number: number) =>
    fetchApi<{ status: string; message: string }>("/reviews/trigger", {
      method: "POST",
      body: JSON.stringify({ repo, pr_number }),
    }),
};
