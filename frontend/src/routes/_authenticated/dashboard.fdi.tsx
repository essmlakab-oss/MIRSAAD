import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Activity, FileSearch, ShieldAlert, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Station3D, type StationAlarm, type StationInspector } from "@/components/station-3d";
import { useFDIContext } from "@/lib/fdi-context";
import { useFDIRun, useSelection } from "@/hooks/use-fdi-run";
import { useXaiHourState, useXaiSeriesState } from "@/hooks/use-xai";
import { fetchStatus, FDIError } from "@/lib/fdi-api";
import { formatAxisHour, hourIsoOf, levelStyle, type FDIStatus } from "@/lib/fdi";
import { FDISummaryStrip } from "@/components/fdi/fdi-summary-strip";
import { FDIControlCenter } from "@/components/fdi/fdi-control-center";
import { FDIEvidenceStrip } from "@/components/fdi/fdi-evidence-strip";
import { FDIIngestButton } from "@/components/fdi/fdi-ingest-button";
import { FDIXAIPanel } from "@/components/fdi/fdi-xai-panel";

export const Route = createFileRoute("/_authenticated/dashboard/fdi")({
  component: FDIPage,
});

/**
 * The three fixed streams the Data Inspector shows, in scene order: the
 * attacked probe, its witness and the weather input.
 *
 * The browser renders exactly these three whatever the dataset holds — they
 * are the streams that decide an hour, not a node per station — and each one
 * carries the real reading taken from the selected row.
 */
const INSPECTOR_NODES = [
  { sensor: "sm5", role: "Target", unit: "m³/m³", decimals: 4 },
  { sensor: "sm10", role: "Witness", unit: "m³/m³", decimals: 4 },
  { sensor: "rain", role: "Weather · ERA5", unit: "mm", decimals: 2 },
] as const;

// ---------------------------------------------------------------------------
// Kind       : FDI analysis page composition
// Description: ingest → score → organize evidence.
//              One selected hour drives everything: the 3D Data Inspector
//              (three fixed nodes carrying that hour's real readings, drawn
//              from the hour's /api/xai payload), the control-center read-out,
//              the 24h evidence strip and the /api/xai panel. Selection lives
//              in `useFDIRun`; the hour payload and the series payload are
//              fetched here (the panel reads the same cached query).
//
//              The XAI panel is gated on `explainable`: hours with
//              "historique insuffisant" have a NaN score — there is no read-out
//              to explain, and their own hint says what to do instead.
function FDIPage() {
  const state = useFDIRun();
  const { point, explainable } = useSelection(state);

  const [status, setStatus] = useState<FDIStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [ingestOpen, setIngestOpen] = useState(false);

  // The model is a multi-hundred-MB artifact: ask once whether it loaded, and
  // lead the page with the answer rather than with an uploader that will 503.
  useEffect(() => {
    let cancelled = false;
    fetchStatus()
      .then((value) => {
        if (!cancelled) setStatus(value);
      })
      .catch((err) => {
        if (!cancelled) {
          setStatus({
            available: false,
            repo_id: null,
            filename: null,
            stations: [],
            threshold: null,
            suspect_threshold: null,
            min_history_days: null,
            detail: err instanceof FDIError ? err.message : "The backend did not answer.",
          });
        }
      })
      .finally(() => {
        if (!cancelled) setStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runId = state.run?.run_id ?? null;
  const row = point?.i ?? null;

  // Z-suffixed hour of the selection — the cache key for everything below.
  const hourIso = point && explainable ? hourIsoOf(point.t) : null;

  // The hour's own /api/xai read-out, and the per-hour series for the strip.
  // Both are cached per (run_id, station, hour); the panel reads the same
  // query instance, so nothing is fetched twice.
  const xaiData = useXaiHourState(runId, point?.station ?? null, hourIso);

  const [stationOverride, setStationOverride] = useState<string | null>(null);
  useEffect(() => setStationOverride(null), [runId]);

  const stripStation = stationOverride ?? point?.station ?? null;
  const series = useXaiSeriesState(runId, stripStation);

  // Runs expire server-side. A 404 on the hour fetch is ambiguous — it can be
  // one hour dropped by the backend's filters (normal, keep the run) or the
  // whole run evicted from the backend store. The series fetch disambiguates:
  // if the run itself was gone, its series endpoint would 404 too. When both
  // fail, the selection is unexplainable — clear the stale run so the page
  // falls back to the ingestion state and the operator can load demo data (or
  // an upload) again.
  useEffect(() => {
    if (xaiData.notFound && series.notFound) {
      toast.error(
        "The analysis session expired. Click Load Demo Dataset — or upload the data again.",
      );
      state.clear();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xaiData.notFound, series.notFound]);

  // Only a confirmed attack lights the beacon. A suspect is worth a cell on
  // the strip, not a flashing lamp — or the operator learns to ignore red.
  const alarm = useMemo(() => {
    if (!point || point.level !== "ATTAQUE") return null;
    return {
      color: levelStyle(point.level).color,
      label: "Attack",
      detail: `${point.station} · ${formatAxisHour(point.t)}`,
    };
  }, [point]);

  // The Data Inspector: the real readings of the selected row on three fixed
  // nodes — the attacked probe, its witness and the weather input. The hour
  // payload is the source (Target = observed sm5; the witness/weather values
  // are the twin's raw feature values for the hour), and a stale response is
  // kept from labelling a newer hour by matching station and hour.
  const inspector = useMemo<StationInspector>(() => {
    const data =
      xaiData.data &&
      xaiData.data.meta.station === point?.station &&
      xaiData.data.meta.hour_iso === hourIso
        ? xaiData.data
        : null;
    const features = data?.shap_twin.features ?? [];
    const featureValue = (key: string) => features.find((f) => f.key === key)?.value ?? null;

    return {
      id: point?.station ?? "dormant",
      hour: point ? formatAxisHour(point.t) : null,
      level: point?.level ?? null,
      color: point ? levelStyle(point.level).color : null,
      nodes: INSPECTOR_NODES.map((entry) => ({
        ...entry,
        value:
          entry.sensor === "sm5"
            ? (data?.summary.observed_sm5 ?? null)
            : featureValue(entry.sensor),
      })),
    };
  }, [point, hourIso, xaiData.data]);

  // The control-center read-out rides on the same payload.
  const readout = useMemo(() => {
    const data = xaiData.data && xaiData.data.meta.hour_iso === hourIso ? xaiData.data : null;
    if (!data) return null;
    return {
      received: data.summary.observed_sm5,
      witness: data.shap_twin.features.find((f) => f.key === "sm10")?.value ?? null,
      twinZ: data.summary.twin_residual_z,
    };
  }, [xaiData.data, hourIso]);

  // The copilot reads the run server-side; this is how it learns which hour
  // the operator is looking at. Cleared with the run, so a question asked
  // after the analysis is gone gets "no dataset loaded" rather than a 404.
  const publishContext = useFDIContext((s) => s.set);
  useEffect(() => {
    publishContext({ runId, row });
  }, [publishContext, runId, row]);

  const unavailable = !statusLoading && status !== null && !status.available;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      {/* ---- Header ------------------------------------------------- */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">FDI detection</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            False-data-injection detection for environmental sensor networks: every hour is scored
            against a digital twin and three physics rules, then explained — rule trace, twin
            decomposition, expected vs actual.
          </p>
        </div>
        <ModelChip loading={statusLoading} status={status} />
      </div>

      {unavailable && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {status?.detail ?? "The detection model is unavailable."} Uploads are rejected until it
          loads.
        </div>
      )}

      {state.error && !state.loading && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {state.error}
        </div>
      )}

      {/* ---- Ingestion (overlay dropzone until a run exists) --------- */}
      {!state.run && !unavailable && (
        <section className="space-y-4">
          {/* The inspector itself, dormant: three grey nodes waiting for a
              dataset. The control-center overlay carries the dropzone, so the
              empty state has exactly one call to action. */}
          <SceneCard
            inspector={inspector}
            overlay={
              <FDIControlCenter
                run={null}
                point={null}
                readout={null}
                loading={state.loading}
                onFile={(file) => void state.loadFile(file)}
                onDemo={() => void state.loadDemo()}
                onClear={state.clear}
              />
            }
          />
          <HowItWorks />
        </section>
      )}

      {/* ---- Run: summary over the control view ---------------------- */}
      {state.run && (
        <>
          <FDISummaryStrip run={state.run} />

          <SceneCard
            inspector={inspector}
            alarm={alarm}
            overlay={
              <FDIControlCenter
                run={state.run}
                point={point}
                readout={readout}
                loading={state.loading}
                onFile={(file) => void state.loadFile(file)}
                onDemo={() => void state.loadDemo()}
                onClear={state.clear}
              />
            }
          />

          {/* The evidence strip replaces the old timeline + scrubber: every
              hour as a colored cell, the selection driving the panel below.
              Beside it, the attack-classification entry: a plain ingestion
              modal that feeds Model 2. */}
          <div className="flex items-start justify-end">
            <FDIIngestButton
              open={ingestOpen}
              onOpenChange={setIngestOpen}
              loading={state.loading}
              onFile={(file) => void state.loadFile(file)}
              onDemo={() => void state.loadDemo()}
            />
          </div>
          <FDIEvidenceStrip
            series={series.data}
            loading={series.loading}
            stations={state.run.dataset.stations}
            station={stripStation ?? state.run.dataset.stations[0] ?? ""}
            onStationChange={setStationOverride}
            selectedHourIso={hourIso}
            threshold={state.run.model.threshold}
            onSelectHour={(hour) => {
              const run = state.run;
              const hit =
                run?.points.find(
                  (p) =>
                    p.station === (stripStation ?? point?.station ?? "") && hourIsoOf(p.t) === hour,
                ) ?? null;
              if (hit) state.select(hit.i);
            }}
          />

          {point && explainable && (
            <FDIXAIPanel runId={state.run.run_id} station={point.station} hourIso={hourIso!} />
          )}

          {point && !explainable && (
            <div className="rounded-2xl border border-dashed border-border/70 bg-muted/20 px-5 py-6 text-center">
              <p className="text-sm font-medium">{levelStyle(point.level).title}</p>
              <p className="mx-auto mt-1 max-w-xl text-xs text-muted-foreground">
                {levelStyle(point.level).hint} This hour carries no score, so there is nothing to
                decompose — pick a scored hour on the evidence strip instead.
              </p>
            </div>
          )}
        </>
      )}

      {/* First paint, before the status call lands: keep the layout still. */}
      {statusLoading && <div className="h-2 w-full animate-pulse rounded-full bg-muted" />}
    </div>
  );
}

/**
 * The Data Inspector in its frame: dormant above the dropzone, live with the
 * control-center overlay once a run exists.
 */
function SceneCard({
  inspector,
  alarm = null,
  overlay,
}: {
  inspector: StationInspector;
  alarm?: StationAlarm | null;
  overlay?: React.ReactNode;
}) {
  return (
    <div
      className="rounded-2xl border border-border/60 bg-card p-3"
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      <Station3D inspector={inspector} alarm={alarm} overlay={overlay} />
      <p className="mt-1 px-1 text-center text-[11px] leading-snug text-muted-foreground">
        Raw sensor context — the detector cross-checks 5cm against 10cm, ERA5 and the twin.
      </p>
    </div>
  );
}

/** Whether the model artifact answered `GET /api/fdi/status`. */
function ModelChip({ loading, status }: { loading: boolean; status: FDIStatus | null }) {
  if (loading) {
    return (
      <span className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-card px-3 py-1 text-xs text-muted-foreground">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
        Loading detector…
      </span>
    );
  }

  const ready = status?.available ?? false;
  const color = ready ? "#22c55e" : "#ef4444";

  return (
    <span
      className="inline-flex max-w-full items-center gap-2 rounded-full border px-3 py-1 text-xs"
      style={{ borderColor: `${color}60`, background: `${color}14`, color }}
      title={status?.detail ?? status?.repo_id ?? undefined}
    >
      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: color }} />
      <span className="truncate">
        {ready
          ? `Detector ready · ${status?.stations.length ?? 0} stations`
          : "Detector unavailable"}
      </span>
    </span>
  );
}

/**
 * What the page will do with a file, stated before the drop.
 *
 * Three cards, one line each: the promise the rest of the page keeps. It only
 * shows while there is no run, because afterwards the run itself is the proof.
 */
function HowItWorks() {
  const steps = [
    {
      icon: UploadCloud,
      title: "1 · Ingest",
      desc: "Hourly station-hour CSV with about 18 days of history before the period to score. Raw USCRN / ERA5 column names are accepted and renamed automatically.",
    },
    {
      icon: ShieldAlert,
      title: "2 · Detect",
      desc: "A digital twin predicts sm5 from witnesses the attacker does not control; paired-step, replay and ECOD rules decide the verdict.",
    },
    {
      icon: FileSearch,
      title: "3 · Explain",
      desc: "Click any hour for the rule trace, the twin's SHAP decomposition, and the expected-vs-actual curve.",
    },
  ];

  return (
    <div className="grid gap-3 md:grid-cols-3">
      {steps.map(({ icon: Icon, title, desc }) => (
        <div
          key={title}
          className="rounded-2xl border border-border/60 bg-card/60 p-4 backdrop-blur"
          style={{ boxShadow: "var(--shadow-elegant)" }}
        >
          <div className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-lg bg-primary/10 text-primary">
              <Icon className="size-4" />
            </span>
            <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
            {title.startsWith("2") && (
              <Activity className="ml-auto size-3.5 text-muted-foreground" />
            )}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{desc}</p>
        </div>
      ))}
    </div>
  );
}
