import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  AlertCircle,
  ArrowDownRight,
  ArrowRightLeft,
  ArrowUpRight,
  Bot,
  ChevronDown,
  ChevronUp,
  Scale,
  Timer,
} from "lucide-react";
import {
  FDI_LEVEL_STYLE,
  formatHourLong,
  formatMoisture,
  formatScore,
  formatSigned,
} from "@/lib/fdi";
import { useXaiHourState } from "@/hooks/use-xai";
import {
  postClassifyAttack,
  type AttackClassification,
  type XaiHour,
} from "@/lib/fdi-api";
import { FDIMetricCards } from "./fdi-metric-cards";
import { FDIAiInsight } from "./fdi-ai-insight";
import { FDITemporalChart } from "./fdi-temporal-chart";

/**
 * The /api/xai panel (§2.2):
 *
 *   ┌ Attack classification (Model 2) — the primary per-hour card: the
 *   │  family pill (or an honest "—" when the head has no answer), the
 *   │  ATTACK / NORMAL verdict, the confidence, and the input contract that
 *   │  explains what the type classifier reads (business level). Replaces the
 *   │  old Model 1 rule trace.
 *   └ Three tabs:
 *       Twin Expectation & Alert Evidence — stacked vertically. First, the
 *                         ECOD family cards (what looked statistically unusual).
 *                         Below them, the SHAP table (why the physics model
 *                         expected something different).
 *       AI Interpretation — auto-fetched executive prose and action.
 *       Temporal        — the hour against the twin's ±12 h window.
 */

type Props = {
  runId: string;
  station: string;
  /** The selected hour, UTC, Z-suffixed. */
  hourIso: string;
};

type Tab = "evidence" | "ai" | "temporal";

export function FDIXAIPanel({ runId, station, hourIso }: Props) {
  const [tab, setTab] = useState<Tab>("evidence");
  const xai = useXaiHourState(runId, station, hourIso);

  const payload = xai.data;
  const niveau = payload?.meta.niveau;
  const style = FDI_LEVEL_STYLE[niveau ?? "normal"];

  return (
    <section className="rounded-2xl border border-border/60 bg-card/70 p-4 shadow-sm backdrop-blur-sm sm:p-5">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-lg font-bold tracking-tight">Why was this hour flagged?</h2>
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{payload?.meta.station ?? station}</span>
            <span aria-hidden>·</span>
            <span title={payload?.meta.hour_iso ?? undefined}>{formatHourLong(hourIso)}</span>
            {payload && (
              <>
                <span aria-hidden>·</span>
                <Badge
                  variant="outline"
                  className="border-transparent px-2 py-0 text-[10px] font-medium"
                  style={{ background: `${style.color}22`, color: style.color }}
                >
                  {style.title}
                </Badge>
                <span className="font-mono text-[10px] opacity-60">
                  score {formatScore(payload.meta.score)} · threshold{" "}
                  {formatScore(payload.meta.threshold)}
                </span>
              </>
            )}
          </p>
        </div>
      </header>

      <FDIMetricCards xai={payload} loading={xai.loading} />

      {xai.loading && <Skeleton className="mt-4 h-24 w-full" />}
      {!xai.loading && xai.error && <ErrorNote message={xai.error} onRetry={xai.reload} />}

      {!xai.loading && payload && (
        <>
          <RuleTrace payload={payload} runId={runId} station={station} hourIso={hourIso} />

          <Tabs value={tab} onValueChange={(value) => setTab(value as Tab)} className="mt-4">
            <TabsList className="inline-flex w-full items-center justify-center gap-1 rounded-full bg-muted/60 p-1">
              <TabsTrigger
                value="evidence"
                data-testid="pill-alert-evidence"
                className="gap-1.5 rounded-full px-3 py-1 text-xs data-[state=active]:bg-background data-[state=active]:shadow-sm"
              >
                <Scale className="size-3.5" /> Twin Expectation & Alert Evidence
              </TabsTrigger>
              <TabsTrigger
                value="ai"
                data-testid="pill-ai-interpretation"
                className="gap-1.5 rounded-full px-3 py-1 text-xs data-[state=active]:bg-background data-[state=active]:shadow-sm"
              >
                <Bot className="size-3.5" /> AI Interpretation
              </TabsTrigger>
              <TabsTrigger
                value="temporal"
                data-testid="pill-temporal"
                className="gap-1.5 rounded-full px-3 py-1 text-xs data-[state=active]:bg-background data-[state=active]:shadow-sm"
              >
                <Timer className="size-3.5" /> Temporal
              </TabsTrigger>
            </TabsList>

            {/* --- Tab 1: Twin Expectation & Alert Evidence ------------- */}
            <TabsContent value="evidence" className="mt-4 space-y-6">
              {/* 1. ECOD: The Outlier Evidence */}
              <div>
                <h3 className="text-sm font-semibold tracking-tight mb-1">Alert Evidence</h3>
                <p className="text-xs text-muted-foreground mb-3">
                  Native decision logic and ECOD per-witness contributions.
                </p>
                <EcodFamilyCards payload={payload} />
              </div>

              <div className="border-t border-border/40" />

              {/* 2. SHAP: The Model's Expectation */}
              <div>
                <h3 className="text-sm font-semibold tracking-tight mb-1">Twin Expectation</h3>
                <p className="text-xs text-muted-foreground mb-3">
                  Top witnesses by exact SHAP contribution to the stage-1 detector score.
                </p>
                <TwinTable payload={payload} />
              </div>
            </TabsContent>

            {/* --- Tab 2: AI Interpretation --------------------------- */}
            <TabsContent value="ai" className="mt-4">
              <FDIAiInsight runId={runId} payload={payload} autoFetch />
            </TabsContent>

            {/* --- Tab 3: Temporal ------------------------------------ */}
            <TabsContent value="temporal" className="mt-4 space-y-3">
              <p className="text-xs text-muted-foreground">
                Digital Twin expected vs observed sm5 — physical proof, not SHAP.
              </p>
              <FDITemporalChart temporal={payload.temporal} hourIso={payload.meta.hour_iso} />
              <p className="text-[10px] leading-snug text-muted-foreground">
                Actual is what the station received; expected is the digital twin's reconstruction
                from the witnesses the attacker does not control.
              </p>
            </TabsContent>
          </Tabs>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Attack classification (Model 2) — the primary per-hour panel
// ---------------------------------------------------------------------------

/** The head returns snake_case classes — present them the way the checklist does. */
function typeLabel(type: string): string {
  const LABELS: Record<string, string> = {
    spike: "Spike",
    bias: "Bias",
    drift: "Drift",
    replay: "Replay",
    fake_storm: "Fake Storm",
    hidden_storm: "Hidden Storm",
    stealth: "Stealth",
  };
  return LABELS[type] ?? type.replaceAll("_", " ");
}

function RuleTrace({
  payload,
  runId,
  station,
  hourIso,
}: {
  payload: XaiHour;
  runId: string;
  station: string;
  hourIso: string;
}) {
  // Model 2 on demand: the classification is computed server-side only when
  // the operator asks for it, but it is fast — it reads the witnesses from
  // the cached run. The three reveal states: value (answered), null (cannot
  // answer), undefined (not yet asked).
  const classify = useMutation({
    mutationFn: () => postClassifyAttack(runId, station, hourIso),
  });
  const shown: AttackClassification | null | undefined =
    payload.rules.attack_classification ?? classify.data;

  return (
    <div className="mt-4 space-y-3">
      {/* Model 2 card border mirrors the accent used throughout the card —
          purple, the Model 2 theme — so it never reads as a plain white
          surface next to the colored ECOD family cards below. 2px purple
          border at 50% opacity + a 12% purple background tint. */}
      <div
        data-testid="model2-block"
        className="rounded-xl border-2 px-4 py-3"
        style={{
          borderColor: "#a855f780",
          backgroundColor: "#a855f71f",
        }}
      >
        {/* The pipeline, in order: the ingest contract is validated, the
            witnesses are built (sensors untouched by the attacker), and
            Model 2 classifies the family. This card replaces the old Model 1
            rule trace as the primary per-hour panel. */}
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-medium uppercase tracking-wider text-purple-400">
            Model 2 — attack classification
          </p>
          <span className="text-[9px] uppercase tracking-wider text-muted-foreground">
            ingest ✓ · witnesses ✓ · classify
          </span>
        </div>

        {shown ? (
          <div className="mt-2">
            {/* family + verdict + confidence, the reading order of the card */}
            <div className="flex flex-wrap items-center gap-2">
              <span
                data-testid="model2-type-pill"
                className="rounded-md border border-purple-500/30 bg-purple-500/10 px-2.5 py-1 text-sm font-semibold text-purple-500 dark:text-purple-400"
              >
                {shown.detected && shown.type ? typeLabel(shown.type) : "—"}
              </span>
              <span
                data-testid={"model2-verdict"}
                className="rounded-md px-2 py-1 text-[11px] font-medium uppercase tracking-wide"
                style={shown.detected
                  ? { background: "rgba(239,68,68,0.12)", color: "#ef4444" }
                  : { background: "rgba(16,185,129,0.12)", color: "#10b981" }}
              >
                {shown.detected ? "ATTACK" : "NORMAL"}
              </span>
              <span
                data-testid="model2-confidence"
                className="text-[11px] font-medium text-muted-foreground tabular-nums"
              >
                confidence {shown.confidence.toFixed(2)} ·{" "}
                {Math.round(shown.confidence * 100)}%
                {shown.type_conf !== null && shown.detected && shown.type
                  ? ` · family ${Math.round(shown.type_conf * 100)}%`
                  : ""}
              </span>
            </div>

            <p data-testid="model2-family-note" className="mt-1 text-[11px] leading-snug text-muted-foreground">
              {shown.detected
                ? shown.type
                  ? "Family predicted by the supervised head over witnesses the attacker does not control."
                  : "Detected by the witness-based V3 detector — no family on record for this frame."
                : "No family: the hour did not clear the attack threshold."}
            </p>
          </div>
        ) : shown === null ? (
          <p data-testid="model2-unavailable" className="mt-1.5 text-xs text-muted-foreground">
            Model 2 could not answer for this run. This usually means no alerts were found, or
            the type classifier artifact is missing.
          </p>
        ) : (
          <button
            type="button"
            data-testid="model2-classify-button"
            onClick={() => classify.mutate()}
            disabled={classify.isPending}
            className="mt-1.5 rounded-md border border-purple-500/40 bg-purple-500/10 px-3 py-1 text-xs font-medium text-purple-600 transition-colors hover:bg-purple-500/20 disabled:opacity-60 dark:text-purple-400"
          >
            {classify.isPending ? "Classifying…" : "Classify with Model 2"}
          </button>
        )}

        <InputContract />
      </div>
    </div>
  );
}

/**
 * The input contract, always visible on the Model 2 card — in business terms:
 * the type classifier names an attack family by playing the station's story
 * against sensors the attacker cannot reach, so a lie at the surface is
 * contradicted by what the deeper layers report.
 */
const CONTRACT_CORE = ["time", "station", "sm5", "sm10", "st5", "t", "rain", "solar"];
const CONTRACT_ERA5 = ["e_t2m", "e_rain", "e_swvl1", "e_stl1"];
const CONTRACT_WITNESSES = ["sm20", "sm50", "st10", "st20", "rh", "sur"];

function InputContract() {
  return (
    <div
      data-testid="model2-input-contract"
      className="mt-2.5 rounded-lg border border-border/50 bg-muted/30 px-3 py-2"
    >
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        How Model 2 names the attack
      </p>
      <div className="mt-1.5 grid gap-1.5 text-[10px] leading-relaxed text-muted-foreground">
        <p>
          <span className="font-medium text-foreground/80">The station's story.</span> The 8 core
          readings (<span className="font-mono text-foreground/80">{CONTRACT_CORE.join(", ")}</span>
          ) — surface moisture at two depths, soil and air temperature, rain, sunshine. These are
          the numbers an attacker would falsify.
        </p>
        <p>
          <span className="font-medium text-foreground/80">The weather outside. </span>
          Independent meteorological estimates (<span className="font-mono text-foreground/80">
          {CONTRACT_ERA5.join(", ")}</span>) built from public weather re-analysis — what the
          conditions <span className="italic">should</span> have produced at that spot.
        </p>
        <p>
          <span className="font-medium text-foreground/80">The witnesses.</span> Six sensors the
          attacker does not touch (<span className="font-mono font-semibold text-foreground">
          {CONTRACT_WITNESSES.join(", ")}</span>): deeper soil moisture, deeper soil temperature,
          air humidity, surface temperature. If someone inflates the surface moisture to fake a
          storm, the deeper layers and the air around the station do not lie along — and the
          <span className="font-medium text-foreground/80"> pattern of that contradiction </span>
          is what tells each attack family apart from the others.
        </p>
      </div>
      <p className="mt-1.5 border-t border-border/40 pt-1.5 text-[10px] leading-snug text-muted-foreground">
        In short: Model 2 reads the claimed readings, the expected weather, and six untouched
        witnesses — the more witnesses a CSV supplies, the sharper the verdict. Missing ones are
        dropped and the family call is made with less evidence, at lower confidence.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Alert Evidence — family cards
// ---------------------------------------------------------------------------

type EcodFeature = XaiHour["ecod"]["features"][number];

function familyMeta(raw: string): { title: string; whatItMeans: string } {
  const map: Record<string, { title: string; whatItMeans: string }> = {
    "Paired-step fingerprint": {
      title: "Paired-step fingerprint",
      whatItMeans:
        "Sudden up-then-down steps on the 5 cm probe that the deeper columns do not mirror — the classic shape of an injected jump.",
    },
    "Depth-relationship": {
      title: "Depth-relationship",
      whatItMeans:
        "Whether the soil depths keep the moisture ratios and deviations they normally share — a broken depth relationship is hard to fake.",
    },
    "Unresolved increment": {
      title: "Unresolved increment",
      whatItMeans:
        "How soil moisture changed over 24 h against what the neighbours imply — shifts that the physical response cannot resolve.",
    },
    "Rain plausibility": {
      title: "Rain plausibility",
      whatItMeans:
        "Whether rain, the moisture rise it should cause, and the reported totals line up — invented or hidden rainfall shows here.",
    },

  };
  return (
    map[raw] ?? {
      title: raw,
      whatItMeans: "Signals the detector grouped in this category.",
    }
  );
}

function witnessDisplayName(key: string, fallback: string): string {
  const map: Record<string, string> = {
    twin_dev: "Reading drift vs. 14-day baseline",
    twin_dev_z: "Reading drift vs. 14-day baseline",
    twin_dev6: "Reading drift vs. 14-day baseline (6 h avg)",
    jump: "One-hour jump in the reading",
    jump_excess: "Jump not explained by the 10 cm depth",
    res_dev: "Deviation from the trend line",
    res_dev6: "Deviation from the trend line (6 h avg)",
    ratio_dev: "Cross-depth ratio anomaly",
  };
  return map[key] ?? fallback.split(" — ")[0] ?? fallback;
}

function formatRarity(p: number): string {
  const oneIn = 1 / Math.max(p, 1e-9);
  if (oneIn >= 2) return `1 in ${Math.round(oneIn).toLocaleString("en-US")}`;
  return `common (${(p * 100).toFixed(0)}%)`;
}

function tailLabel(tail: string): string {
  if (tail === "upper") return "above normal";
  if (tail === "lower") return "below normal";
  return tail;
}

function EcodFamilyCards({ payload }: { payload: XaiHour }) {
  const [expandedFamily, setExpandedFamily] = useState<string | null>(null);

  const featuresByFamily = useMemo(() => {
    const map = new Map<string, EcodFeature[]>();
    for (const f of payload.ecod.features) {
      const list = map.get(f.family) ?? [];
      list.push(f);
      map.set(f.family, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => b.contribution - a.contribution);
    }
    return map;
  }, [payload.ecod.features]);

  const families = payload.ecod.families;
  const peak = Math.max(1e-9, ...families.map((f) => Math.abs(f.signed_contribution)));

  return (
    <div data-testid="ecod-families" className="space-y-2">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {families.map((family) => {
          const meta = familyMeta(family.family);
          const members = featuresByFamily.get(family.family) ?? [];
          const isOpen = expandedFamily === family.family;
          const positive = family.signed_contribution >= 0;
          // Family accent: red pushes the alert up, sky pulls it down. The
          // whole tile is bordered in that color (2px, 50% opacity) with a
          // 12% tint of the same color behind it.
          const color = positive ? "#ef4444" : "#38bdf8";
          const barPct = Math.min(100, (Math.abs(family.signed_contribution) / peak) * 100);
          const shareLabel = positive ? "of the alert signal" : "offsetting the alert";

          return (
            <div
              key={family.family}
              data-testid="ecod-family-card"
              className="overflow-hidden rounded-xl border-2 transition-all"
              style={{
                borderColor: `${color}80`,
                backgroundColor: `${color}1f`,
              }}
            >
              <button
                type="button"
                onClick={() => setExpandedFamily(isOpen ? null : family.family)}
                aria-expanded={isOpen}
                className="w-full px-3 py-3 text-left transition-colors hover:bg-white/5"
              >
                <p
                  className="truncate text-[11px] font-semibold leading-tight"
                  title={family.family}
                >
                  {meta.title}
                </p>

                <p
                  className="mt-1.5 text-2xl font-bold tabular-nums leading-none"
                  style={{ color }}
                >
                  {family.share_pct.toFixed(1)}%
                </p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{shareLabel}</p>

                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted/40">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${barPct}%`,
                      background: color,
                      opacity: 0.85,
                    }}
                  />
                </div>

                <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>
                    <span className="font-medium tabular-nums" style={{ color }}>
                      {formatSigned(family.signed_contribution, 1)}
                    </span>{" "}
                    weight · {members.length} signal{members.length === 1 ? "" : "s"}
                  </span>
                  {isOpen ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
                </div>
              </button>

              {isOpen && (
                <div
                  data-testid="ecod-family-details"
                  className="border-t px-3 py-2"
                  style={{ borderColor: `${color}33` }}
                >
                  <p className="text-[10px] leading-snug text-muted-foreground">
                    {meta.whatItMeans}
                  </p>

                  {members.length > 0 ? (
                    <ul className="mt-2 space-y-2">
                      {members.map((m) => (
                        <li key={m.key} data-testid="ecod-witness-row" className="text-[11px]">
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="truncate font-medium" title={m.label}>
                              {witnessDisplayName(m.key, m.label)}
                            </span>
                            <span className="shrink-0 font-semibold tabular-nums" style={{ color }}>
                              {m.contribution.toFixed(1)}
                            </span>
                          </div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[10px] text-muted-foreground">
                            <span className="font-mono opacity-60">{m.key}</span>
                            <span aria-hidden>·</span>
                            <span>reading {fmtValue(m.value)} m³/m³</span>
                            <span aria-hidden>·</span>
                            <span>{formatRarity(m.p)}</span>
                            <span aria-hidden>·</span>
                            <span>{tailLabel(m.tail)}</span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2 text-[10px] italic text-muted-foreground">
                      No individual signals recorded for this category.
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="text-[10px] leading-snug text-muted-foreground">
        Each category weighs how strongly its signals pushed the alert up (red) or pulled it down
        (blue). Click a card to see the individual signals behind it.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sortable helpers (used by TwinTable)
// ---------------------------------------------------------------------------

type SortState<K extends string> = { key: K; dir: "asc" | "desc" };

function cycle<K extends string>(
  state: SortState<K> | null,
  key: K,
  fallback: SortState<K>,
): SortState<K> {
  if (!state || state.key !== key) return fallback;
  return { key, dir: state.dir === "desc" ? "asc" : "desc" };
}

function Th<K extends string>({
  label,
  sortKey,
  sort,
  onSort,
  className = "",
}: {
  label: string;
  sortKey: K;
  sort: SortState<K> | null;
  onSort: (key: K) => void;
  className?: string;
}) {
  const active = sort?.key === sortKey;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort.dir === "desc" ? "descending" : "ascending") : "none"}
      className={`px-2 py-1.5 text-left ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 rounded text-[10px] font-medium uppercase tracking-wider ${
          active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        {label}
        {active && <span aria-hidden>{sort.dir === "desc" ? "↓" : "↑"}</span>}
      </button>
    </th>
  );
}

// ---------------------------------------------------------------------------
// Twin Expectation
// ---------------------------------------------------------------------------

function TwinTable({ payload }: { payload: XaiHour }) {
  const twin = payload.shap_twin;
  type Row = XaiHour["shap_twin"]["features"][number];
  const [sort, setSort] = useState<SortState<"rank" | "label" | "value" | "shap" | "direction">>({
    key: "shap",
    dir: "desc",
  });

  const rows = useMemo(() => {
    const accessors: Record<string, (row: Row) => number | string> = {
      rank: (row) => row.rank,
      label: (row) => row.label,
      value: (row) => row.value ?? -Infinity,
      shap: (row) => row.shap,
      direction: (row) => row.shap,
    };
    const pick = accessors[sort.key];
    return [...twin.features].sort((a, b) => {
      const va = pick(a);
      const vb = pick(b);
      if (typeof va === "number" && typeof vb === "number") {
        const cmp =
          sort.key === "shap" || sort.key === "direction" || sort.key === "value"
            ? Math.abs(vb as number) - Math.abs(va as number)
            : vb - va;
        return sort.dir === "desc" ? cmp : -cmp;
      }
      return sort.dir === "desc"
        ? String(vb).localeCompare(String(va))
        : String(va).localeCompare(String(vb));
    });
  }, [twin.features, sort]);

  return (
    <div data-testid="twin-table" className="space-y-2">
      <p className="text-[11px] text-muted-foreground">
        TreeExplainer on the stage-1 detector (M1) · baseline {formatMoisture(twin.base_value)}
        m³/m³ · {twin.features.length} features, ranked by |SHAP|
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[500px] rounded-lg border border-border/60 text-xs">
          <thead className="border-b border-border/60 bg-muted/30">
            <tr>
              <Th
                label="Rank"
                sortKey="rank"
                sort={sort}
                onSort={(k) => setSort(cycle(sort, k, { key: k, dir: "asc" }))}
                className="w-14"
              />
              <Th
                label="Witness"
                sortKey="label"
                sort={sort}
                onSort={(k) => setSort(cycle(sort, k, { key: k, dir: "asc" }))}
              />
              <Th
                label="Value"
                sortKey="value"
                sort={sort}
                onSort={(k) => setSort(cycle(sort, k, { key: k, dir: "desc" }))}
                className="text-right"
              />
              <Th
                label="SHAP"
                sortKey="shap"
                sort={sort}
                onSort={(k) => setSort(cycle(sort, k, { key: k, dir: "desc" }))}
                className="text-right"
              />
              <Th
                label="Direction"
                sortKey="direction"
                sort={sort}
                onSort={(k) => setSort(cycle(sort, k, { key: k, dir: "desc" }))}
              />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-b border-border/30 last:border-0">
                <td className="px-2 py-1.5 tabular-nums text-muted-foreground">{row.rank}</td>
                <td className="max-w-56 truncate px-2 py-1.5 font-medium" title={row.label}>
                  {row.label}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmtValue(row.value)}</td>
                <td className="px-2 py-1.5 text-right font-semibold tabular-nums">
                  {formatSigned(row.shap, 4)}
                </td>
                <td className="px-2 py-1.5">
                  {row.shap > 0 ? (
                    <span className="inline-flex items-center gap-1 text-red-500">
                      <ArrowUpRight className="size-3" /> pushes up
                    </span>
                  ) : row.shap < 0 ? (
                    <span className="inline-flex items-center gap-1 text-sky-500">
                      <ArrowDownRight className="size-3" /> pushes down
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-muted-foreground">
                      <ArrowRightLeft className="size-3" /> neutral
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] leading-snug text-muted-foreground">
        Red pushes the twin's expected sm5 up, blue down. The twin predicts sm5 without ever seeing
        it — only witnesses the attacker does not control — so the expectation is the anchor every
        other check hangs on.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function fmtValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 100) return value.toFixed(1);
  return value.toFixed(3);
}

function ErrorNote({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mt-4 flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-10 text-center">
      <AlertCircle className="size-5 text-destructive" />
      <p className="text-sm text-muted-foreground">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="text-xs font-medium text-primary underline-offset-4 hover:underline"
      >
        Retry
      </button>
    </div>
  );
}