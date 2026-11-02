import { useEffect, useMemo, useRef, useState } from "react";
import { Rocket } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  arenaApi,
  ARENA_COLORS,
  BUCKET_STYLE,
  FAMILY_LABEL,
  STATION_LABEL,
  fmtDuration,
  type ArenaFamilyKey,
  type ArenaJob,
  type ArenaMeta,
  type ArenaResult,
} from "@/lib/arena-api";
import { cn } from "@/lib/utils";

/**
 * Launcher — many attacks at once. The engine places them in 2025 (≥ 30 days
 * apart per station so they do not overlap), runs the defender on each and
 * sorts every miss. A row replays its attack in the Duel view.
 */

const card = "rounded-2xl border border-border/60 bg-card p-4";
const shadow = { boxShadow: "var(--shadow-elegant)" };

const parseList = (s: string) =>
  s
    .split(/[,;\s]+/)
    .map((x) => Number(x))
    .filter((x) => Number.isFinite(x) && x !== 0);

export function ArenaLauncher({
  meta,
  ready,
  onReplay,
  onBatchDone,
}: {
  meta: ArenaMeta | null;
  ready: boolean;
  onReplay: (jobId: string, attackId: string) => void;
  onBatchDone: () => void;
}) {
  const [families, setFamilies] = useState<ArenaFamilyKey[]>(["bias", "drift", "spike"]);
  const [stations, setStations] = useState<string[]>(["NY_Ithaca_13_E", "CA_Fallbrook_5_NE"]);
  const [amps, setAmps] = useState("0.01, 0.03, 0.06");
  const [durs, setDurs] = useState("24, 96, 240");
  const [maxN, setMaxN] = useState(24);
  const [job, setJob] = useState<ArenaJob | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<{ job_id: string; results: ArenaResult[] } | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  // the most recent recorded batch fills the table before anything is launched
  useEffect(() => {
    if (!ready) return;
    arenaApi
      .runs()
      .then((r) => setHistory(r[0] ?? null))
      .catch(() => setHistory(null));
  }, [ready]);

  useEffect(
    () => () => {
      if (timer.current) window.clearInterval(timer.current);
    },
    [],
  );

  async function launch() {
    setError(null);
    try {
      const { job_id } = await arenaApi.batch({
        families,
        stations,
        amplitudes: parseList(amps),
        durations: parseList(durs).map(Math.round),
        max_n: maxN,
      });
      setJobId(job_id);
      setJob({ id: job_id, status: "queued", done: 0, total: maxN });
      if (timer.current) window.clearInterval(timer.current);
      timer.current = window.setInterval(async () => {
        try {
          const J = await arenaApi.job(job_id);
          setJob(J);
          if (J.status === "done" || J.status === "error") {
            if (timer.current) window.clearInterval(timer.current);
            if (J.status === "done") onBatchDone();
          }
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }, 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const shown = useMemo(() => {
    if (job?.status === "done" && job.results && jobId)
      return { jobId, rows: job.results, live: true };
    if (history) return { jobId: history.job_id, rows: history.results, live: false };
    return null;
  }, [job, jobId, history]);

  const rows = useMemo(() => {
    const R = (shown?.rows ?? []).filter((r) => r.status === "applied");
    return filter === "all" ? R : R.filter((r) => r.family === filter);
  }, [shown, filter]);

  const toggle = <T,>(arr: T[], v: T) =>
    arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
  const running = job !== null && (job.status === "queued" || job.status === "running");

  return (
    <div className="grid gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
      <section className={card} style={shadow}>
        <h2 className="mb-3 text-sm font-semibold tracking-tight">Batch of attacks</h2>

        <Label>Attack types</Label>
        <Chips
          items={(meta?.families ?? []).map((f) => [f.key, f.label] as const)}
          on={families}
          onToggle={(k) => setFamilies(toggle(families, k as ArenaFamilyKey))}
        />
        <Label>Stations</Label>
        <Chips
          items={(meta?.stations ?? []).map((s) => [s, STATION_LABEL[s] ?? s] as const)}
          on={stations}
          onToggle={(k) => setStations(toggle(stations, k))}
        />
        <Label>Sizes (m³/m³, signs are drawn at random)</Label>
        <input className={inputCls} value={amps} onChange={(e) => setAmps(e.target.value)} />
        <Label>Durations (hours)</Label>
        <input className={inputCls} value={durs} onChange={(e) => setDurs(e.target.value)} />
        <Label>Max attacks</Label>
        <input
          className={inputCls}
          type="number"
          min={1}
          max={400}
          value={maxN}
          onChange={(e) => setMaxN(Number(e.target.value))}
        />

        <Button
          className="mt-1 w-full"
          disabled={!ready || running || families.length === 0 || stations.length === 0}
          onClick={() => void launch()}
          style={{ background: ARENA_COLORS.attack }}
        >
          <Rocket /> {running ? "Running…" : "Launch batch"}
        </Button>
        {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
        <p className="mt-3 text-[11px] leading-snug text-muted-foreground">
          Attacks are placed in 2025, at least 30 days apart per station. About 8–30 s per station
          group; results are added to the Dashboard.
        </p>
      </section>

      <section className={cn(card, "min-w-0")} style={shadow}>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold tracking-tight">
            {shown?.live
              ? `Batch ${shown.jobId}`
              : shown
                ? `Recorded batch ${shown.jobId}`
                : "No batch yet"}
          </h2>
          {job && (
            <span className="text-xs text-muted-foreground">
              {job.status === "error"
                ? `Error: ${job.error}`
                : `${job.done} / ${job.total} attacks · ${job.status}`}
            </span>
          )}
        </div>
        {job && (
          <div className="mb-3 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full transition-[width]"
              style={{
                width: `${job.total ? (100 * job.done) / job.total : 0}%`,
                background: ARENA_COLORS.attack,
              }}
            />
          </div>
        )}

        <div className="mb-2 flex flex-wrap gap-1.5">
          {["all", ...Object.keys(FAMILY_LABEL)].map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilter(k)}
              aria-pressed={filter === k}
              className={cn(
                "rounded-full border px-2.5 py-0.5 text-[11px]",
                filter === k
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border hover:bg-accent",
              )}
            >
              {k === "all" ? "All" : FAMILY_LABEL[k]}
            </button>
          ))}
        </div>

        <div className="max-h-[34rem] overflow-auto rounded-xl border border-border/60">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-muted/90 text-[10px] uppercase tracking-wider text-muted-foreground backdrop-blur">
              <tr>
                {[
                  "Type",
                  "Station",
                  "Size",
                  "Duration",
                  "Result",
                  "Delay",
                  "Predicted type",
                  "Registry",
                ].map((h) => (
                  <th key={h} className="px-2.5 py-2 text-left font-semibold">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const b = BUCKET_STYLE[r.bucket ?? ""] ?? null;
                return (
                  <tr
                    key={r.id}
                    className="cursor-pointer border-t border-border/50 hover:bg-accent/50"
                    onClick={() => shown && onReplay(shown.jobId, r.id)}
                    title="Replay this attack in the Duel view"
                  >
                    <td className="px-2.5 py-1.5">{FAMILY_LABEL[r.family] ?? r.family}</td>
                    <td className="px-2.5 py-1.5">{STATION_LABEL[r.station] ?? r.station}</td>
                    <td className="px-2.5 py-1.5 font-mono">
                      {r.damage_max === null || r.damage_max === undefined
                        ? "–"
                        : r.damage_max.toFixed(3)}
                    </td>
                    <td className="px-2.5 py-1.5">
                      {r.duration_h ? fmtDuration(r.duration_h) : "–"}
                    </td>
                    <td className="px-2.5 py-1.5">
                      <span
                        className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                        style={
                          r.detected
                            ? { color: ARENA_COLORS.detected, background: "#10b9811a" }
                            : { color: ARENA_COLORS.missed, background: "#ef44441a" }
                        }
                      >
                        {r.detected ? "Detected" : "Missed"}
                      </span>
                    </td>
                    <td className="px-2.5 py-1.5 font-mono">
                      {r.detected && r.delay_h !== null && r.delay_h !== undefined
                        ? r.delay_h <= 0
                          ? "at start"
                          : `+${Math.round(r.delay_h)} h`
                        : "–"}
                    </td>
                    <td className="px-2.5 py-1.5">
                      {r.type_pred ? (FAMILY_LABEL[r.type_pred] ?? r.type_pred) : "–"}
                    </td>
                    <td className="px-2.5 py-1.5">
                      {b && (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="h-2 w-2 rounded-full" style={{ background: b.color }} />
                          {b.label}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-8 text-center text-muted-foreground">
                    {running
                      ? "The engine is running the attacks…"
                      : "Launch a batch to fill this table."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Click a row to replay that attack, step by step, in the Duel view.
        </p>
      </section>
    </div>
  );
}

const inputCls =
  "mb-3 h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </div>
  );
}

function Chips({
  items,
  on,
  onToggle,
}: {
  items: readonly (readonly [string, string])[];
  on: string[];
  onToggle: (k: string) => void;
}) {
  return (
    <div className="mb-3 flex flex-wrap gap-1.5">
      {items.map(([k, label]) => (
        <button
          key={k}
          type="button"
          onClick={() => onToggle(k)}
          aria-pressed={on.includes(k)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs transition-colors",
            on.includes(k)
              ? "border-primary bg-primary/10 text-primary"
              : "border-border bg-background hover:bg-accent",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
