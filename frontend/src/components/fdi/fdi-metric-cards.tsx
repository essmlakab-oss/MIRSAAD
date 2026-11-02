import { ShieldCheck, TrendingDown, TrendingUp, Zap } from "lucide-react";
import { formatMoisture } from "@/lib/fdi";
import { Skeleton } from "@/components/ui/skeleton";
import type { XaiHour } from "@/lib/fdi-api";

/**
 * The three numbers every explanation is about, above the explanation pills:
 * what the digital twin expected, what the sensor actually sent, and how far
 * apart they are — all read from the hour's own /api/xai summary, never
 * recomputed in the browser.
 *
 * Above the cards sits the verdict row: the verdict badge (Alert red /
 * Suspect amber / Normal green), the residual-z chip — shown only on flagged
 * hours (niveau SUSPECT/ATTAQUE) whose twin residual reaches |z| ≥ 3; see
 * CHANGELOG "S-1" — and — on demo runs only — the ground-truth chip telling
 * the audience whether the hour sits inside an injected window. An uploaded
 * CSV has no ground truth; the chip stays silent rather than implying one.
 */

type Props = {
  /** The hour payload; null while it is loading or the hour is unscorable. */
  xai: XaiHour | null;
  loading?: boolean;
};

function fallbackCards() {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-6 w-20 rounded-full" />
        <Skeleton className="h-6 w-24 rounded-full" />
        <Skeleton className="h-6 w-36 rounded-full" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Skeleton className="h-[4.6rem] w-full rounded-xl bg-black/5 backdrop-blur-md dark:bg-black/40" />
        <Skeleton className="h-[4.6rem] w-full rounded-xl bg-black/5 backdrop-blur-md dark:bg-black/40" />
        <Skeleton className="h-[4.6rem] w-full rounded-xl bg-black/5 backdrop-blur-md dark:bg-black/40" />
      </div>
    </div>
  );
}

export function FDIMetricCards({ xai, loading = false }: Props) {
  if (loading || !xai || xai.summary.observed_sm5 === null || xai.summary.expected_sm5 === null) {
    return fallbackCards();
  }

  const { summary, meta } = xai;
  const niveau = meta.niveau;
  const attack = niveau === "ATTAQUE";
  const expected = summary.expected_sm5;
  const observed = summary.observed_sm5;
  if (expected === null || observed === null) return fallbackCards();
  const deviation = deviationInfo(summary.deviation_pct, expected, observed);
  const trend = observed - expected;

  return (
    <div className="space-y-2">
      {/* --- Verdict row ---------------------------------------------- */}
      <div className="flex flex-wrap items-center gap-2">
        <span
          data-testid="verdict-badge"
          className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
          style={verdictBadgeStyle(niveau)}
        >
          {verdictLabel(niveau)}
        </span>

        {/* S-1 display fix: on synthetic demo data twin_residual_z is
            out-of-distribution on unflagged hours (artifact sigma is
            per-station and real-data-trained), so |z| alone is not a
            trustworthy escalation signal. The chip renders only when the
            detector actually flagged the hour (niveau SUSPECT or ATTAQUE)
            AND |z| >= 3. See CHANGELOG "S-1 — twin_residual_z on synthetic
            demo (deferred to Phase 3)". */}
        {(niveau === "ATTAQUE" || niveau === "SUSPECT") &&
          summary.twin_residual_z !== null &&
          Math.abs(summary.twin_residual_z) >= 3 && (
            <span
              data-testid="residual-chip"
              className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400"
            >
              <Zap className="size-3" />
              Residual {Math.abs(summary.twin_residual_z).toFixed(1)}σ
            </span>
          )}

        {meta.ground_truth !== null && (
          <span
            data-testid="truth-chip"
            className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium"
            style={
              meta.ground_truth === "attack"
                ? { borderColor: "#ef444460", background: "#ef444414", color: "#ef4444" }
                : { borderColor: "#10b98160", background: "#10b98114", color: "#10b981" }
            }
          >
            <ShieldCheck className="size-3" />
            {meta.ground_truth === "attack"
              ? "Attack inserted · demo ground truth"
              : "Clean window · demo ground truth"}
          </span>
        )}
      </div>

      {/* --- The three cards ------------------------------------------ */}
      <div className="grid gap-3 sm:grid-cols-3">
        <Card
          label="Expected (Twin)"
          value={formatMoisture(summary.expected_sm5)}
          accent="text-foreground"
          hint="What the witnesses imply the probe should read"
        />
        <Card
          label="Observed (Sensor)"
          value={formatMoisture(summary.observed_sm5)}
          accent={attack ? "text-red-400" : "text-foreground"}
          hint="What the 5 cm probe actually transmitted"
        />
        <Card
          label="Deviation"
          value={deviation}
          accent={attack ? "text-red-400" : "text-foreground"}
          icon={
            trend > 0 ? (
              <TrendingUp className="h-4 w-4" />
            ) : trend < 0 ? (
              <TrendingDown className="h-4 w-4" />
            ) : undefined
          }
          hint="How far the reading sits from the twin"
        />
      </div>
    </div>
  );
}

function Card({
  label,
  value,
  hint,
  accent,
  icon,
}: {
  label: string;
  value: string;
  hint: string;
  accent: string;
  icon?: React.ReactNode;
}) {
  return (
    <div
      title={hint}
      // Added a dark tint to the background and border for the "blacky" glass effect
      className="group relative overflow-hidden rounded-xl border border-black/20 bg-black/[0.07] px-4 py-3 shadow-sm backdrop-blur-xl transition-all hover:bg-black/[0.07] hover:shadow-md dark:border-white/10 dark:bg-black/40 dark:hover:bg-black/50"
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      {/* Subtle shine overlay for the glass reflection */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-white/30 to-transparent opacity-40 dark:from-white/5" />

      <div className="relative z-10">
        <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        <p className={`mt-1 flex items-baseline gap-1 text-2xl font-semibold tabular-nums ${accent}`}>
          {value}
          {icon && <span className="size-4 self-center">{icon}</span>}
        </p>
      </div>
    </div>
  );
}

function deviationInfo(deviationPct: number | null, expected: number, observed: number): string {
  if (deviationPct !== null && Number.isFinite(deviationPct)) {
    return `${deviationPct >= 0 ? "+" : ""}${deviationPct.toFixed(1)}%`;
  }

  // Server guard: |expected| ≈ 0 makes a percentage meaningless — quote the
  // moisture delta itself (same fallback legend as before, now server-owned).
  const total = observed - expected;
  return `${total >= 0 ? "+" : ""}${total.toFixed(4)}`;
}

/** The detector's own French vocabulary drives the badge, exactly as everywhere. */
function verdictLabel(niveau: XaiHour["meta"]["niveau"]): string {
  switch (niveau) {
    case "ATTAQUE":
      return "Alert";
    case "SUSPECT":
      return "Suspect";
    case "normal":
      return "Normal";
    default:
      return "No history";
  }
}

function verdictBadgeStyle(niveau: XaiHour["meta"]["niveau"]): React.CSSProperties {
  switch (niveau) {
    case "ATTAQUE":
      return { borderColor: "#ef444460", background: "#ef444414", color: "#ef4444" };
    case "SUSPECT":
      return { borderColor: "#f59e0b60", background: "#f59e0b14", color: "#f59e0b" };
    case "normal":
      return { borderColor: "#10b98160", background: "#10b98114", color: "#10b981" };
    default:
      return { borderColor: "#77777760", background: "#77777714", color: "#777777" };
  }
}