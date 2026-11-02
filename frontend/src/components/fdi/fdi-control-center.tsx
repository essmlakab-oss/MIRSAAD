import { AlertTriangle, Clock, Gauge, ShieldAlert, ShieldCheck, X } from "lucide-react";
import type { FDIPredictResponse, FDIPoint } from "@/lib/fdi";
import {
  formatAxisHour,
  formatHourLong,
  formatMoisture,
  formatScore,
  levelStyle,
  ruleFor,
} from "@/lib/fdi";
import { FDIDropzone } from "./fdi-dropzone";

/**
 * The control center floating over the 3D scene: ingestion on top, the
 * selected hour's read-out beneath it.
 *
 * The scene carries the alarm — the beacon beside the mast turns red while
 * the selected hour is an attack — because that is what you notice from
 * across a room. This panel is what you read once it has your attention.
 *
 * Three states. Input: nothing loaded yet, so the panel is the dropzone and
 * the demo button — the first thing an operator touches. Loading: progress
 * while the two-stage detector runs. Loaded: a three-metric summary of the
 * dataset and the live read-out of the selected hour, which connects the 3D
 * scene to the timeline below it.
 */

type Props = {
  run: FDIPredictResponse | null;
  point: FDIPoint | null;
  /** The selected hour's read-out, drawn from its /api/xai payload. */
  readout: { received: number | null; witness: number | null; twinZ: number | null } | null;
  loading: boolean;
  onFile: (file: File) => void;
  onDemo: () => void;
  /** Clear the run back to the input state (the small X on the summary). */
  onClear: () => void;
};

export function FDIControlCenter({ run, point, readout, loading, onFile, onDemo, onClear }: Props) {
  if (!run) {
    return (
      <Shell>
        <div className="pointer-events-auto">
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium">
            <Gauge className="h-3.5 w-3.5" />
            Control Center
          </p>
          <FDIDropzone onFile={onFile} onDemo={onDemo} loading={loading} />
        </div>
      </Shell>
    );
  }

  return (
    <Shell accent={point && point.level === "ATTAQUE" ? levelStyle(point.level).color : undefined}>
      <div className="pointer-events-auto">
        {/* Loaded state: the dataset at a glance, clearable in one click. */}
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] font-medium">Control Center</p>
          <button
            type="button"
            onClick={onClear}
            title="Clear dataset"
            aria-label="Clear dataset"
            className="rounded-full border border-white/25 p-1 opacity-70 transition-all hover:bg-white/10 hover:opacity-100"
          >
            <X className="h-3 w-3" />
          </button>
        </div>

        <DatasetGrid run={run} />

        {/* Read-out of the selected hour — kept beneath the summary grid on
            purpose: the numbers below belong to the hour the scene is
            spotlighting. */}
        <div className="mt-2 border-t border-white/20 pt-2">
          {point ? (
            <ReadOut run={run} point={point} readout={readout} />
          ) : (
            <div className="space-y-1">
              <SummaryLine summary={run.summary} />
              <p className="text-[10px] leading-snug opacity-60">
                Click an hour on the evidence strip to explain it.
              </p>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}

function DatasetGrid({ run }: { run: FDIPredictResponse }) {
  const { dataset } = run;
  const range =
    dataset.start && dataset.end
      ? `${formatAxisHour(dataset.start)} → ${formatAxisHour(dataset.end)}`
      : "—";
  const source =
    dataset.source === "demo" ? "Demo dataset" : (dataset.filename ?? "Uploaded CSV");

  return (
    <div className="mt-1.5 space-y-1">
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        <Metric label="Rows" value={dataset.rows.toLocaleString()} />
        <Metric label="Stations" value={String(dataset.stations.length)} />
        <Metric label="Time Range" value={range} className="col-span-2 truncate" />
      </div>
      <p className="truncate text-[10px] opacity-60" title={dataset.filename ?? "Demo dataset"}>
        {source}
      </p>
    </div>
  );
}

function Metric({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <p className="text-[9px] uppercase tracking-wider opacity-60">{label}</p>
      <p className="truncate text-[11px] font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function ReadOut({
  run,
  point,
  readout,
}: {
  run: FDIPredictResponse;
  point: FDIPoint;
  readout: { received: number | null; witness: number | null; twinZ: number | null } | null;
}) {
  const style = levelStyle(point.level);
  // Only an attack lights the lamp; a suspect is worth a cell on the strip
  // but not a flashing beacon, or the operator learns to ignore red.
  const alarm = point.level === "ATTAQUE";

  return (
    <div>
      <div className="flex items-center gap-1.5">
        <LevelIcon level={point.level} color={style.color} />
        <span className="text-[11px] font-semibold" style={{ color: style.color }}>
          {style.title}
        </span>
        <span
          className="ml-auto rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide tabular-nums"
          style={{ background: `${style.color}22`, color: style.color }}
        >
          {formatScore(point.score)}
        </span>
      </div>

      <p className="mt-0.5 text-[10px] leading-snug opacity-60">
        {point.station} · {formatHourLong(point.t)}
      </p>

      {/*
        The pair the whole platform is about: what the attacker sent, and what
        the twin expected from witnesses he does not control. The gap between
        them is what every explanation below decomposes.
      */}
      <div className="mt-1.5 space-y-0.5">
        <Row label="sm5 received" value={formatMoisture(readout?.received ?? point.sm5)} />
        <Row label="sm10 witness" value={formatMoisture(readout?.witness ?? null)} />
        {readout?.twinZ != null && (
          <Row label="twin residual" value={`${readout.twinZ.toFixed(2)} σ`} />
        )}
      </div>

      <RuleLine run={run} point={point} />

      {alarm && (
        <p className="mt-1 text-[10px] font-medium leading-snug" style={{ color: style.color }}>
          Confirmed — quarantine this reading.
        </p>
      )}

      <SummaryLine summary={run.summary} className="mt-1.5 border-t border-white/15 pt-1.5" />
    </div>
  );
}

/**
 * Which rule fired — looked up from the candidate list, not guessed. v3
 * candidates may carry `rule: null`, in which case there is nothing honest
 * to display and the line is hidden entirely.
 */
function RuleLine({ run, point }: { run: FDIPredictResponse; point: FDIPoint }) {
  const candidate = run.candidates.find((entry) => entry.i === point.i);
  const rule = ruleFor(candidate?.rule ?? null);
  if (!rule) return null;

  return (
    <div className="mt-1.5 flex items-baseline justify-between gap-2 border-t border-white/15 pt-1.5">
      <span className="text-[10px] opacity-60">Rule</span>
      <span className="text-[11px] font-medium" style={{ color: rule.color }}>
        {rule.short}
      </span>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[10px] opacity-60">{label}</span>
      <span className="text-[11px] font-medium tabular-nums">{value}</span>
    </div>
  );
}

function SummaryLine({
  summary,
  className,
}: {
  summary: FDIPredictResponse["summary"];
  className?: string;
}) {
  return (
    <div className={`flex flex-wrap items-center gap-x-2.5 gap-y-1 ${className ?? ""}`}>
      <SummaryPill color="#ef4444" value={summary.attaque} label="attack" />
      <SummaryPill color="#f59e0b" value={summary.suspect} label="suspect" />
      <SummaryPill color="#10b981" value={summary.normal} label="normal" />
      <SummaryPill color="#777777" value={summary.insufficient_history} label="no history" />
    </div>
  );
}

function SummaryPill({ color, value, label }: { color: string; value: number; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-[10px] opacity-80">
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      <span className="tabular-nums" style={{ color }}>
        {value}
      </span>
      {label}
    </span>
  );
}

function LevelIcon({ level, color }: { level: string; color: string }) {
  const Icon =
    level === "ATTAQUE"
      ? ShieldAlert
      : level === "SUSPECT"
        ? AlertTriangle
        : level === "historique insuffisant"
          ? Clock
          : ShieldCheck;
  return <Icon className="h-4 w-4 shrink-0" style={{ color }} />;
}

function Shell({ children, accent }: { children: React.ReactNode; accent?: string }) {
  return (
    <div
      data-tour="fdi-control-center"
      className="pointer-events-none absolute left-2.5 top-2.5 w-[19.5rem] max-w-[calc(100%-1.25rem)] rounded-2xl border border-white/20 bg-white/10 px-3.5 py-3 text-xs shadow-2xl backdrop-blur-xl transition-all"
      style={{
        borderColor: accent ? `${accent}80` : undefined,
        boxShadow: accent ? `0 0 24px ${accent}33` : undefined,
      }}
    >
      {children}
    </div>
  );
}
