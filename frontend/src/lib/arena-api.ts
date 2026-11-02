/**
 * Attack Arena API client — attacker vs defender.
 *
 * Same contract as lib/fdi-api.ts: the session cookie authenticates every call
 * (`credentials: "include"`) and a 401 gets one silent refresh attempt.
 * Shapes mirror backend/app/routers/arena.py and backend/app/arena/engine.py.
 */

import { apiUrl } from "./api";

export class ArenaError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ArenaError";
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    /* fall through */
  }
  return `Request failed (${response.status})`;
}

function send(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(apiUrl(path), {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  let response = await send(path, init);
  if (response.status === 401 && retry) {
    const refreshed = await send("/api/auth/refresh", { method: "POST" });
    if (refreshed.ok) response = await send(path, init);
  }
  if (!response.ok) throw new ArenaError(await readError(response), response.status);
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

export type ArenaFamilyKey =
  "spike" | "bias" | "drift" | "replay" | "fake_storm" | "hidden_storm" | "stealth";

export interface ArenaStatus {
  status: "idle" | "loading" | "ready" | "unavailable";
  detail: string | null;
  assets: string;
}

export interface ArenaFamily {
  key: ArenaFamilyKey;
  label: string;
  default: { sensors: string; shape: string | null; duration_h: number; amplitude: number | null };
  limits: { min_h: number; max_h: number; max_amplitude: number | null };
}

export interface ArenaMeta {
  stations: string[];
  families: ArenaFamily[];
  sensors: string[];
  shapes: string[];
  threshold: number;
  model: {
    name: string;
    f1_cyber: number;
    f1_ours: number;
    precision: number;
    false_alarms: number;
  };
}

export interface ArenaScenario {
  family: ArenaFamilyKey;
  station: string;
  start: string;
  amplitude?: number;
  duration_h?: number;
  shape?: string;
  sensors?: string;
  params?: Record<string, unknown>;
  id?: string;
}

export interface ArenaWitness {
  key: string;
  text: string;
  values: (number | null)[];
  peak_inside: number;
}

export interface ArenaDecision {
  detected: boolean;
  verdict: string;
  first_alert_delay_h: number | null;
  hours_detected: number | null;
  max_proba: number;
  type_top3: { type: string; label: string; p: number }[];
  reasons: string[];
  damage_max: number;
  damage_int: number;
}

export interface ArenaFrames {
  status: "applied";
  scenario: ArenaScenario;
  manifest: Record<string, unknown> & {
    family: ArenaFamilyKey;
    station: string;
    start: string;
    duration_h: number;
    changed?: string[];
  };
  station: string;
  threshold: number;
  compute_s: number;
  time: string[];
  attack_window: [string, string];
  series: {
    clean5: (number | null)[];
    att5: (number | null)[];
    clean10: (number | null)[];
    att10: (number | null)[];
    clean_rain: (number | null)[];
    att_rain: (number | null)[];
    expected5: (number | null)[];
    sm20: (number | null)[];
    label: number[];
  };
  untouched: string[];
  witnesses: ArenaWitness[];
  scores: { stage1: (number | null)[]; proba: (number | null)[]; alert: boolean[] };
  decision: ArenaDecision;
}

export interface ArenaRejected {
  status: "rejected";
  reason: string;
}

export interface ArenaResult {
  id: string;
  family: ArenaFamilyKey;
  station: string;
  status: string;
  start?: string;
  amplitude?: number | null;
  duration_h?: number;
  damage_max?: number | null;
  detected?: boolean | null;
  delay_h?: number | null;
  hours_detected?: number | null;
  type_pred?: string | null;
  bucket?: string | null;
  reason?: string | null;
}

export interface ArenaFamilyScore {
  family: ArenaFamilyKey;
  label: string;
  attempts: number;
  detected: number;
  rate: number;
  got_through: number;
  max_undetected_damage: number | null;
  mean_delay_h: number | null;
  hours_detected: number;
}

export interface ArenaScoreboard {
  families: ArenaFamilyScore[];
  totals: {
    attempts?: number;
    detected?: number;
    rate?: number;
    known_limit?: number;
    train?: number;
    reference?: number;
  };
}

export type ArenaFrontier = Record<
  string,
  { size: string; mid: number; rate: number; n: number }[]
>;

export interface ArenaJob {
  id: string;
  status: "queued" | "running" | "done" | "error";
  done: number;
  total: number;
  results?: ArenaResult[] | null;
  summary?: ArenaScoreboard;
  error?: string | null;
  elapsed_s?: number;
}

export interface ArenaBatchSpec {
  families?: ArenaFamilyKey[];
  stations?: string[];
  amplitudes?: number[];
  durations?: number[];
  max_n?: number;
  seed?: number;
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

export const arenaApi = {
  status: () => request<ArenaStatus>("/api/arena/status"),
  meta: () => request<ArenaMeta>("/api/arena/meta"),
  simulate: (scenario: ArenaScenario) =>
    request<ArenaFrames | ArenaRejected>("/api/arena/simulate", {
      method: "POST",
      body: JSON.stringify(scenario),
    }),
  batch: (generate: ArenaBatchSpec) =>
    request<{ job_id: string; n: number }>("/api/arena/batch", {
      method: "POST",
      body: JSON.stringify({ generate }),
    }),
  job: (id: string) => request<ArenaJob>(`/api/arena/jobs/${encodeURIComponent(id)}`),
  scoreboard: () => request<ArenaScoreboard>("/api/arena/scoreboard"),
  frontier: () => request<ArenaFrontier>("/api/arena/frontier"),
  runs: () => request<{ job_id: string; results: ArenaResult[] }[]>("/api/arena/runs"),
  replay: (job: string, attack: string) =>
    request<ArenaFrames | ArenaRejected>(
      `/api/arena/replay/${encodeURIComponent(job)}/${encodeURIComponent(attack)}`,
    ),
};

// ---------------------------------------------------------------------------
// Vocabulary shared by the three views
// ---------------------------------------------------------------------------

export const STATION_LABEL: Record<string, string> = {
  AZ_Tucson_11_W: "Tucson, AZ",
  OK_Goodwell_2_SE: "Goodwell, OK",
  MS_Newton_5_ENE: "Newton, MS",
  NY_Ithaca_13_E: "Ithaca, NY",
  CA_Fallbrook_5_NE: "Fallbrook, CA",
};

export const FAMILY_LABEL: Record<string, string> = {
  spike: "Spike",
  bias: "Bias",
  drift: "Drift",
  replay: "Replay",
  fake_storm: "Fake storm",
  hidden_storm: "Hidden storm",
  stealth: "Stealth",
};

export const BUCKET_STYLE: Record<string, { label: string; color: string; hint: string }> = {
  detected: { label: "Detected", color: "#10b981", hint: "caught by the defender" },
  known_limit: {
    label: "Known limit",
    color: "#94a3b8",
    hint: "missed, but as small as natural noise — reported, never learned",
  },
  train: {
    label: "Train",
    color: "#f59e0b",
    hint: "missed and detectable — variants go into the next training set",
  },
  reference: {
    label: "Reference",
    color: "#3b82f6",
    hint: "missed and detectable — kept apart to prove the next model improved",
  },
};

/** Attacker = warm, defender = the console's blue, verdicts reuse the FDI level colours. */
export const ARENA_COLORS = {
  attack: "#f97316",
  defend: "oklch(0.60 0.14 245)",
  truth: "#94a3b8",
  detected: "#10b981",
  missed: "#ef4444",
};

export const fmtDuration = (h: number) =>
  h < 48 ? `${h} h` : `${(h / 24).toFixed(h % 24 ? 1 : 0)} days`;
export const fmtAmp = (a: number | null | undefined) =>
  a === null || a === undefined ? "real data" : `${a >= 0 ? "+" : ""}${a.toFixed(3)} m³/m³`;
