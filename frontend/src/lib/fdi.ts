/**
 * The FDI model's output, and how the UI reads it.
 *
 * These types mirror backend/app/fdi/schemas.py exactly — the level
 * vocabulary in particular is the detector's own French, not a translation:
 * `ATTAQUE`, `SUSPECT`, `normal`, `historique insuffisant` are written by the
 * v3 detector and a retrain could change them, so the UI styles them by
 * key and never parses the prose around them.
 */

// ---------------------------------------------------------------------------
// Levels, rules and their presentation
// ---------------------------------------------------------------------------

export type FDILevel = "ATTAQUE" | "SUSPECT" | "normal" | "historique insuffisant";

/** Stable rule keys, mapped from the model's `raison` text on the backend. */
export type FDIRuleKey = "marches" | "replay" | "ecod" | "no_hist" | "none";

export const FDI_LEVEL_STYLE: Record<
  FDILevel,
  { title: string; short: string; color: string; hint: string; rank: number }
> = {
  ATTAQUE: {
    title: "Confirmed attack",
    short: "Attack",
    color: "#ef4444",
    hint: "A physical rule fired — paired steps or an exact replay. Reject the reading.",
    // Lowest rank wins the default selection: the operator wants the attack.
    rank: 0,
  },
  SUSPECT: {
    title: "Suspect — possible drift",
    short: "Suspect",
    color: "#f59e0b",
    hint: "Incoherent with the witnesses. Investigate — often a slow drift the raw rules miss.",
    rank: 1,
  },
  normal: {
    title: "Normal",
    short: "Normal",
    color: "#10b981",
    hint: "The witnesses corroborate the value received.",
    rank: 2,
  },
  "historique insuffisant": {
    title: "Insufficient history",
    short: "No history",
    color: "#777777",
    hint: "The detector needs about 18 days before the period it scores. Provide more history upstream.",
    rank: 3,
  },
};

export const FDI_RULES: Record<
  FDIRuleKey,
  { title: string; short: string; desc: string; action: string; color: string }
> = {
  marches: {
    title: "Rule 1 · Paired steps",
    short: "Paired steps",
    desc: "An upward jump followed by a downward jump of similar amplitude (within 140 h) on the 5 cm probe, not explained by the 10 cm probe and held at a constant offset from the digital twin.",
    action: "Reject / quarantine the reading — confirmed attack.",
    color: "#ef4444",
  },
  replay: {
    title: "Rule 2 · Exact copy (replay)",
    short: "Replay",
    desc: "A 48 h sequence of the 5 cm probe has already been observed identically (rounded to 1e-3) at least 24 h earlier: the value is being replayed from a buffer.",
    action: "Reject / quarantine the reading — confirmed attack.",
    color: "#ef4444",
  },
  ecod: {
    title: "Rule 3 · ECOD (incoherent witnesses)",
    short: "ECOD",
    desc: "Ten derived witness features (five families: paired-step fingerprint, depth-relationship, unresolved increment, rain plausibility, replay) sit in the tail of the distribution of normal reference hours: possible slow drift.",
    action: "Investigate — suspect, often a drift the raw rules miss.",
    color: "#f59e0b",
  },
  no_hist: {
    title: "Insufficient history",
    short: "No history",
    desc: "The detector's sliding reference (14 days shifted by 4 days) needs about 18 days of history before the period being scored.",
    action: "Provide more history upstream of the period to analyse.",
    color: "#777777",
  },
  none: {
    title: "No rule triggered",
    short: "Normal",
    desc: "Nothing unusual: the witnesses (10 cm probe, ERA5, rain, season) corroborate the value received.",
    action: "Accept the data.",
    color: "#22c55e",
  },
};

export function levelStyle(level: string | null | undefined) {
  return FDI_LEVEL_STYLE[(level ?? "normal") as FDILevel] ?? FDI_LEVEL_STYLE.normal;
}

/**
 * The rule behind a candidate — or null when the backend records none (v3
 * candidates carry `rule: null`; the rule key now lives in the type head's
 * classification, not in the candidate list). A null input or an unknown key
 * means there is nothing to display.
 */
export function ruleFor(key: string | null | undefined): (typeof FDI_RULES)[FDIRuleKey] | null {
  if (key === null || key === undefined || key === "") return null;
  return FDI_RULES[key as FDIRuleKey] ?? null;
}

/** Hours the detector could not score. Never a default selection for XAI. */
export function isExplainable(point: { level: string; score: number | null }): boolean {
  return point.level !== "historique insuffisant" && point.score !== null;
}

// ---------------------------------------------------------------------------
// Response shapes, mirroring backend/app/fdi/schemas.py
// ---------------------------------------------------------------------------

export interface FDIModelInfo {
  ready: boolean;
  framework: string;
  repo_id: string | null;
  filename: string | null;
  stations: string[];
  twin_features: string[];
  inputs: string[];
  threshold: number;
  suspect_threshold: number | null;
  min_history_days: number;
}

export interface FDIDatasetInfo {
  rows: number;
  scored: number;
  insufficient_history: number;
  stations: string[];
  start: string | null;
  end: string | null;
  renamed_columns: Record<string, string>;
  source: "upload" | "demo";
  filename: string | null;
}

export interface FDISummary {
  hours: number;
  attaque: number;
  suspect: number;
  normal: number;
  insufficient_history: number;
  alerts: number;
  max_score: number | null;
  mean_score: number | null;
  threshold: number;
}

export interface FDIPoint {
  i: number;
  t: string;
  station: string;
  score: number | null;
  level: FDILevel;
  alert: number;
  /** Additive: the raw sm5 reading behind the verdict, for hover read-outs. */
  sm5: number | null;
}

export interface FDICandidate {
  i: number;
  t: string;
  station: string;
  score: number | null;
  level: FDILevel;
  rule: FDIRuleKey | null;
}

export interface FDIPredictResponse {
  run_id: string;
  created_at: string;
  model: FDIModelInfo;
  dataset: FDIDatasetInfo;
  summary: FDISummary;
  points: FDIPoint[];
  candidates: FDICandidate[];
}

export interface FDIDemoResponse extends FDIPredictResponse {
  injected_attacks: Record<string, [string | null, string | null]>;
}

export interface FDIStatus {
  available: boolean;
  repo_id: string | null;
  filename: string | null;
  stations: string[];
  threshold: number | null;
  suspect_threshold: number | null;
  min_history_days: number | null;
  detail: string | null;
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/** Moisture is a small number: four decimals carries the signal, more is noise. */
export function formatMoisture(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

export function formatSigned(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}`;
}

/**
 * The score is a probability in [0,1] (stage-2 M2 output). Two decimals
 * carries the signal; thresholds are quoted the same way.
 */
export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(2);
}

export function formatHour(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatHourLong(iso: string | null | undefined): string {
  if (!iso) return "—";
  // S-3: hour_iso is Z-suffixed UTC on the contract (F1). Every surface
  // formats that instant in a pinned UTC locale so the flagged-card header,
  // the strip cells and the control-center read-out render the IDENTICAL
  // string for the same hour_iso, on any operator machine. A Z-less string
  // (raw out["time"]) must not be reinterpreted in the local zone — hint it
  // as UTC before parsing.
  const date = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-US", {
    timeZone: "UTC",
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Compact axis label: "29 Mar 04:00" loses the year, which every hour shares. */
export function formatAxisHour(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.getDate()} ${date.toLocaleString(undefined, { month: "short" })} ${String(
    date.getHours(),
  ).padStart(2, "0")}:00`;
}

/** hour_iso is Z-suffixed UTC on /api/xai (F1); points carry it, guard anyway. */
export function hourIsoOf(t: string): string {
  return /(?:Z|[+-]\d{2}:?\d{2})$/.test(t) ? t : `${t}Z`;
}

/** Shorten a pretty feature label for an axis: drop the " — explanation" tail. */
export function shortFeature(label: string): string {
  const dash = label.indexOf(" — ");
  const raw = dash > 0 ? label.slice(0, dash) : label;
  return humanFeature(raw);
}

/**
 * Human names for the twin's features — the audience should never have to
 * parse an engineering key on an axis. `sm10_d6` reads "10cm Moisture
 * (6h Change)"; anything unmapped falls back to the raw key.
 *
 * Mirrors app/fdi/detector.py FEATURE_LABELS: the raw keys, the delta window
 * lengths (1, 3, 6, 24 h) and the rain totals (6, 24, 72 h) must stay in
 * step with what the backend engineers.
 */
const FEATURE_HUMAN: Record<string, string> = {
  sm5: "5cm Moisture",
  sm10: "10cm Moisture",
  st5: "5cm Soil Temp",
  t: "Air Temperature",
  rain: "Rain",
  solar: "Solar Radiation",
  e_t2m: "ERA5 Air Temp",
  e_rain: "ERA5 Rain",
  e_swvl1: "ERA5 Soil Moisture",
  e_stl1: "ERA5 Soil Temp",
  hr: "Hour of Day",
  doy_s: "Season (sin)",
  doy_c: "Season (cos)",
};

for (const hours of [1, 3, 6, 24]) {
  FEATURE_HUMAN[`sm10_d${hours}`] = `10cm Moisture (${hours}h Change)`;
}
for (const window of [6, 24, 72]) {
  FEATURE_HUMAN[`rain_${window}`] = `Rain Total (${window}h)`;
  FEATURE_HUMAN[`erain_${window}`] = `ERA5 Rain Total (${window}h)`;
}

export function humanFeature(raw: string): string {
  return FEATURE_HUMAN[raw] ?? raw;
}

/**
 * The data contract, stated before the operator uploads rather than after the
 * backend rejects a file. Raw USCRN / ERA5 column names are accepted too —
 * the backend renames them (SOIL_MOISTURE_5 -> sm5, t2m -> e_t2m, …).
 */
export const FDI_SCHEMA_HINT =
  "Columns: time, station, sm5, sm10, st5, t, rain, solar, e_t2m, e_rain, e_swvl1, e_stl1. " +
  "ISO timestamps, hourly, one row per station-hour. Include about 18 days before the period " +
  "you want scored — the detector's sliding reference needs it. Raw USCRN / ERA5 names are " +
  "accepted and renamed automatically.";
