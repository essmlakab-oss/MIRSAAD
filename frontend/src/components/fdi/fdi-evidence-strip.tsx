import { useEffect, useMemo, useRef } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import type { XaiSeries } from "@/lib/fdi-api";
import { formatHourLong, levelStyle } from "@/lib/fdi";

/**
 * S-2 attentive: the series rows may carry the additive top_ecod_family
 * field (string | null). The frozen generated types (§5 MD5 pin) are one
 * regeneration behind, so the field is read through a local extension until
 * the next regen — unknown JSON fields flow through at runtime.
 */
type SeriesHour = XaiSeries["hours"][number] & { top_ecod_family?: string | null };

/**
 * The evidence strip (§2.5): one cell per hour — the detector's verdicts laid
 * out as a wall of evidence instead of a chart you have to read.
 *
 * Cell color is the niveau (red attack, amber suspect, green normal, grey
 * awaiting history); cell height is the hour's combined pressure — the larger
 * of the twin residual magnitude (scaled by 5σ) and the ECOD score (scaled by
 * twice its threshold), capped at 1. The vertical cursor snaps to the
 * selected hour and every click selects that hour, driving the panel below.
 * A muted sparkline repeats the ECOD pressure without axes. Plain CSS grid,
 * no chart library — this is a read-out, not a plot.
 */

type Props = {
  series: XaiSeries | null;
  loading: boolean;
  /** Extra stations, when the run holds more than the selected one. */
  stations: string[];
  station: string;
  onStationChange: (station: string) => void;
  /** Z-suffixed hour of the selected point — the cursor snaps to it. */
  selectedHourIso: string | null;
  onSelectHour: (hourIso: string) => void;
  /** The detector's own score threshold, from the run's model info. */
  threshold: number | null;
};

/** Cell geometry: pressure fraction → px height between 4 and 48. */
const MIN_BAR = 4;
const MAX_BAR = 48;

export function FDIEvidenceStrip({
  series,
  loading,
  stations,
  station,
  onStationChange,
  selectedHourIso,
  onSelectHour,
  threshold,
}: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  const hours = useMemo(() => series?.hours ?? [], [series]);

  // Keep the cursor in view: when the selection (or station) changes, slide
  // the scroller so the selected cell is visible without touching page scroll.
  useEffect(() => {
    if (!selectedHourIso || !scrollerRef.current) return;
    const index = hours.findIndex((hour) => hour.t === selectedHourIso);
    if (index < 0) return;
    const scroller = scrollerRef.current;
    const cell = scroller.children[index] as HTMLElement | undefined;
    if (!cell) return;
    scroller.scrollLeft = cell.offsetLeft - scroller.clientWidth / 2 + cell.offsetWidth / 2;
  }, [selectedHourIso, hours, station]);

  return (
    <div
      data-tour="fdi-evidence-strip"
      data-testid="evidence-strip"
      className="rounded-2xl border border-border/60 bg-card p-4"
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-tight">
          24h evidence strip — twin residual and ECOD pressure
        </h2>
        {stations.length > 1 && (
          <div className="flex flex-wrap gap-1.5" data-testid="strip-station-switcher">
            {stations.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => onStationChange(name)}
                aria-pressed={name === station}
                className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                  name === station
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border/60 bg-muted/30 text-muted-foreground hover:bg-muted/60"
                }`}
              >
                {name}
              </button>
            ))}
          </div>
        )}
      </div>

      {loading && <Skeleton className="h-24 w-full" />}

      {!loading && hours.length > 0 && (
        <>
          <div
            ref={scrollerRef}
            data-testid="strip-cells"
            className="flex min-w-0 items-end gap-px overflow-x-auto pb-1"
            style={{ scrollbarWidth: "thin" }}
          >
            {hours.map((hour) => {
              const style = levelStyle(hour.niveau);
              const inactive = hour.niveau === "historique insuffisant";
              const selected = hour.t === selectedHourIso;
              // Height formula follows §2.5. On synthetic demo data |z|/5
              // saturates; color (niveau) is primary signal. See CHANGELOG S-1.
              const pressure = pressureOf(hour.ecod_score, hour.twin_residual_z, threshold);
              const height = MIN_BAR + pressure * (MAX_BAR - MIN_BAR);

              return (
                <button
                  key={hour.t}
                  type="button"
                  title={cellTitle(
                    hour.t,
                    hour.niveau,
                    hour.ecod_score,
                    hour.twin_residual_z,
                    (hour as SeriesHour).top_ecod_family ?? null,
                  )}
                  aria-label={cellTitle(
                    hour.t,
                    hour.niveau,
                    hour.ecod_score,
                    hour.twin_residual_z,
                    (hour as SeriesHour).top_ecod_family ?? null,
                  )}
                  aria-pressed={selected}
                  onClick={() => onSelectHour(hour.t)}
                  className="group relative flex h-16 w-2.5 shrink-0 items-end justify-center rounded-sm pb-0.5 transition-colors"
                  style={
                    selected
                      ? { outline: "2px solid var(--foreground)", outlineOffset: 1 }
                      : undefined
                  }
                >
                  <span
                    aria-hidden
                    className="w-1.5 rounded-sm"
                    style={{
                      height,
                      background: style.color,
                      opacity: inactive ? 0.35 : selected ? 1 : 0.8,
                      boxShadow: selected ? `0 0 6px ${style.color}80` : undefined,
                    }}
                  />
                </button>
              );
            })}
          </div>

          <Sparkline hours={hours} />

          <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {(["ATTAQUE", "SUSPECT", "normal", "historique insuffisant"] as const).map(
                (level) => (
                  <span key={level} className="inline-flex items-center gap-1">
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{
                        background: levelStyle(level).color,
                        opacity: level === "historique insuffisant" ? 0.35 : 1,
                      }}
                    />
                    {levelStyle(level).short}
                  </span>
                ),
              )}
            </div>
            <span>{hours.length} hours · click a cell to explain that hour</span>
          </div>
        </>
      )}

      {!loading && hours.length === 0 && (
        <p className="py-6 text-center text-xs text-muted-foreground">
          No scored hours for {station} yet.
        </p>
      )}
    </div>
  );
}

/** min(1, max(|z|/5, score / (2 * threshold))); nulls contribute nothing. */
function pressureOf(score: number | null, z: number | null, threshold: number | null): number {
  const parts: number[] = [];
  if (z !== null && Number.isFinite(z)) parts.push(Math.abs(z) / 5);
  if (score !== null && Number.isFinite(score) && threshold !== null && threshold > 0) {
    parts.push(score / (2 * threshold));
  }
  if (parts.length === 0) return 0;
  return Math.min(1, Math.max(...parts));
}

function cellTitle(
  t: string,
  niveau: string,
  score: number | null,
  z: number | null,
  family: string | null,
): string {
  const parts = [
    formatHourLong(t),
    levelStyle(niveau).short,
    score === null ? "score —" : `score ${score.toFixed(3)}`,
    z === null ? "twin residual z —" : `twin residual z ${z.toFixed(2)}`,
    family === null ? "family —" : `family ${family}`,
  ];
  return parts.join(" · ");
}

/** Muted, axis-free repetition of the ECOD pressure. */
function Sparkline({ hours }: { hours: XaiSeries["hours"] }) {
  const width = 720;
  const height = 28;
  const scores = hours.map((hour) => hour.ecod_score);
  const peak = Math.max(1e-9, ...scores.map((score) => (score === null ? 0 : score)));

  // Nulls break the line into segments rather than plunging to the floor.
  const segments: string[] = [];
  let current: string[] = [];
  scores.forEach((score, index) => {
    if (score === null) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    const x = (index / Math.max(1, scores.length - 1)) * width;
    const y = height - (score / peak) * (height - 2) - 1;
    current.push(`${current.length === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
  });
  if (current.length > 1) segments.push(current.join(" "));

  return (
    <svg
      data-testid="strip-sparkline"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="mt-1 h-7 w-full"
      role="img"
      aria-label="ECOD score sparkline across the run"
    >
      {segments.map((segment, index) => (
        <path
          key={index}
          d={segment}
          fill="none"
          stroke="currentColor"
          strokeWidth={1}
          opacity={0.35}
          className="text-muted-foreground"
        />
      ))}
    </svg>
  );
}
