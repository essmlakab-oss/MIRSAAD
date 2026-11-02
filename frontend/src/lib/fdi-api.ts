/**
 * The FDI API: six endpoints, one run store behind them.
 *
 * The shape of the interaction is set by the backend (README_app.md §7.3): an
 * upload is scored once and becomes a `run_id`, and every explanation after
 * that names the run and a row. Nothing uploads twice, and `df` / `H` never
 * reach the browser at all.
 *
 * Auth is the session cookie, so every call sends `credentials: "include"` and
 * a 401 gets one silent refresh attempt — the same contract lib/auth.ts uses.
 */

import { apiUrl } from "./api";
import type { components } from "@/types/fdi-api";
import type { FDIDemoResponse, FDIPredictResponse, FDIStatus } from "./fdi";

export class FDIError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "FDIError";
  }
}

/** FastAPI reports errors as {detail}: a string, or a list of field errors. */
async function readError(response: Response): Promise<string> {
  const fallback = `Request failed (${response.status})`;

  try {
    const body = await response.json();
    const detail = body?.detail;

    if (typeof detail === "string") return detail;

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          typeof item?.msg === "string" ? item.msg.replace(/^Value error, /, "") : null,
        )
        .filter(Boolean);
      if (messages.length > 0) return messages.join(". ");
    }

    return fallback;
  } catch {
    return fallback;
  }
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(path), {
    ...init,
    // Without this the browser withholds the session cookies and every call
    // looks signed out.
    credentials: "include",
    headers: {
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
}

/** One call, with a single silent retry through /api/auth/refresh on a 401. */
async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  let response = await send(path, init);

  if (response.status === 401 && retry && path !== "/api/auth/refresh") {
    const refreshed = await send("/api/auth/refresh", { method: "POST" });
    if (refreshed.ok) {
      response = await send(path, init);
    }
  }

  if (!response.ok) {
    throw new FDIError(await readError(response), response.status);
  }

  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// The API
// ---------------------------------------------------------------------------

/** Whether the model loaded. The page calls this before showing the uploader. */
export function fetchStatus(): Promise<FDIStatus> {
  return request<FDIStatus>("/api/fdi/status");
}

/** Score an uploaded CSV. */
export function postPredict(file: File): Promise<FDIPredictResponse> {
  const form = new FormData();
  form.append("file", file);

  return request<FDIPredictResponse>("/api/fdi/predict", { method: "POST", body: form });
}

/** Score the synthetic dataset (34 days x 2 stations, 3 injected attacks). */
export function postDemo(): Promise<FDIDemoResponse> {
  return request<FDIDemoResponse>("/api/fdi/demo", { method: "POST" });
}

// ---------------------------------------------------------------------------
// The /api/xai contract (Phase 1 §1.7), read back from the generated module:
// names cannot drift from app/fdi/schemas.py without a diff landing in
// src/types/fdi-api.d.ts first.
// ---------------------------------------------------------------------------

/**
 * Model 2 (v3): the 7-class type head's answer for flagged hours, attached
 * additively to the rules block (null until POST /classify runs). The
 * generated §1.7 types are frozen and predate the field, so it is declared
 * here. The 7 classes are the source-of-truth vocabulary from the backend —
 * the old smooth_bias / stealth_era5 classes no longer exist.
 */
export type AttackType =
  | "spike"
  | "bias"
  | "drift"
  | "replay"
  | "fake_storm"
  | "hidden_storm"
  | "stealth";

export type AttackClassification = {
  detected: boolean;
  confidence: number;
  /** Attack class predicted by Model 2's type head (null when not detected). */
  type: AttackType | null;
  /** Head's own confidence for the chosen class (null when no class was named). */
  type_conf: number | null;
};

export type XaiHour = components["schemas"]["XaiResponse"] & {
  rules: components["schemas"]["XaiResponse"]["rules"] & {
    /** Model 2 answer, or null when the classifier cannot answer. */
    attack_classification?: AttackClassification | null;
  };
};
export type XaiSeries = components["schemas"]["XaiSeries"];
export type XaiSeriesHour = components["schemas"]["XaiSeriesPoint"];
export type XaiRuleDetailsMarches = components["schemas"]["XaiRuleDetailsMarches"];
export type XaiRuleDetailsReplay = components["schemas"]["XaiRuleDetailsReplay"];
export type XaiRuleDetailsEcod = components["schemas"]["XaiRuleDetailsEcod"];

/** One hour, everything about it: the panel's own read-out. */
export function fetchXaiHour(runId: string, station: string, hourIso: string): Promise<XaiHour> {
  return request<XaiHour>(
    `/api/xai/${encodeURIComponent(runId)}/${encodeURIComponent(station)}/${encodeURIComponent(hourIso)}`,
  );
}

/** Cheap per-hour strip data (level, ECOD score, twin residual z). */
export function fetchXaiSeries(runId: string, station: string): Promise<XaiSeries> {
  return request<XaiSeries>(
    `/api/xai/${encodeURIComponent(runId)}/series/${encodeURIComponent(station)}`,
  );
}

/** §1.8: the client posts the panel's own response object back, re-validated server-side. */
export type InterpretXaiRequest = {
  payload: XaiHour;
};

/**
 * Phase 3 business-level interpretation — validated server-side,
 * banned-word-free. Mirrors backend/app/fdi/schemas.py::InterpretXaiResponse
 * exactly: a field renamed on one side is a 502 on the other.
 */
export type InterpretXaiResponse = {
  /** Executive summary, 1–2 sentences, ≤250 chars. */
  resume: string;
  /** Must equal the payload's summary.verdict. */
  verdict: "ALERT" | "NORMAL";
  /** Plain-language narrative, 2–4 sentences, ≤350 chars. */
  what_happened: string;
  /** 1–4 short strings, each ≤180 chars: "factor X did Y". */
  key_factors: string[];
  /** Business consequence, 1–2 sentences, ≤250 chars. */
  what_it_means: string;
  /** Imperative next step, 1 sentence, ≤180 chars. */
  recommended_action: string;
  confidence: "low" | "medium" | "high";
  /** 1 = first attempt, 2 = retried after validation rejection. */
  attempt: number;
  /** Short opaque id for the audit log line. */
  request_id: string;
};

/** One Groq turn: the selected hour's XAI read-out as an executive summary. */
export function postInterpretXai(body: InterpretXaiRequest): Promise<InterpretXaiResponse> {
  return request<InterpretXaiResponse>("/api/ai/interpret-xai", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * Model 2 on demand: classify the run's most-likely attack hour (Phase 3).
 * Fast now — it reads from the cached run's stored witnesses; resolves to
 * null when no alerts were found or the type classifier artifact is missing.
 */
export function postClassifyAttack(
  runId: string,
  station: string,
  hourIso: string,
): Promise<AttackClassification | null> {
  return request<{ attack_classification: AttackClassification | null }>(
    `/api/xai/${runId}/${station}/${hourIso}/classify`,
    { method: "POST" },
  ).then((r) => r.attack_classification);
}
