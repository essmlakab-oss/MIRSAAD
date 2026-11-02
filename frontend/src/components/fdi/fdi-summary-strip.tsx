import { Database, FileSpreadsheet, ShieldAlert } from "lucide-react";
import type { FDIPredictResponse } from "@/lib/fdi";
import { formatHour, formatScore } from "@/lib/fdi";

/**
 * The run's headline numbers: what was ingested, and what came out.
 *
 * Placed directly under the ingestion zone so the value of a drop is visible
 * before anything is clicked — counts on the left (the verdict), provenance on
 * the right (what was scored). Everything here comes from the predict
 * response; nothing is recomputed in the browser, so the strip can never
 * disagree with the charts below it.
 */

type Props = { run: FDIPredictResponse };

export function FDISummaryStrip({ run }: Props) {
  const { summary, dataset, model } = run;

  return (
    <div
      data-tour="fdi-summary"
      className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border/60 bg-border/40 sm:grid-cols-3 lg:grid-cols-6"
    >
      <Stat
        icon={<ShieldAlert className="size-3.5" />}
        label="Confirmed attacks"
        value={summary.attaque}
        color="#ef4444"
        hint="physics rule fired"
      />
      <Stat label="Suspect hours" value={summary.suspect} color="#f59e0b" hint="possible drift" />
      <Stat
        label="Alerts raised"
        value={summary.alerts}
        color="var(--primary)"
        hint="attack + suspect"
      />
      <Stat
        label="Hours scored"
        value={summary.hours}
        color="var(--color-foreground)"
        hint={`${summary.insufficient_history} awaiting history`}
      />
      <Meta
        icon={<FileSpreadsheet className="size-3.5" />}
        label={dataset.source === "demo" ? "Demo dataset" : (dataset.filename ?? "Upload")}
        lines={[
          `${dataset.rows.toLocaleString()} rows · ${dataset.stations.length} station${dataset.stations.length === 1 ? "" : "s"}`,
          dataset.start
            ? `${formatHour(dataset.start)} → ${formatHour(dataset.end)}`
            : "no time column",
        ]}
      />
      <Meta
        icon={<Database className="size-3.5" />}
        label="Detector"
        lines={[
          `threshold ${formatScore(summary.threshold)} · peak ${formatScore(summary.max_score)}`,
          model.repo_id ? model.repo_id.split("/").pop()! : model.framework,
        ]}
      />
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  color,
  hint,
}: {
  icon?: React.ReactNode;
  label: string;
  value: number;
  color: string;
  hint: string;
}) {
  return (
    <div className="flex items-center gap-3 bg-card px-4 py-3">
      {icon && <span style={{ color }}>{icon}</span>}
      <div className="min-w-0">
        <div className="text-xl font-semibold tabular-nums leading-none" style={{ color }}>
          {value.toLocaleString()}
        </div>
        <div className="mt-1 truncate text-[11px] font-medium text-foreground">{label}</div>
        <div className="truncate text-[10px] text-muted-foreground">{hint}</div>
      </div>
    </div>
  );
}

function Meta({ icon, label, lines }: { icon: React.ReactNode; label: string; lines: string[] }) {
  return (
    <div className="flex items-center gap-3 bg-card px-4 py-3">
      <span className="text-muted-foreground">{icon}</span>
      <div className="min-w-0">
        <div className="truncate text-[11px] font-medium text-foreground">{label}</div>
        {lines.map((line) => (
          <div key={line} className="truncate text-[10px] text-muted-foreground">
            {line}
          </div>
        ))}
      </div>
    </div>
  );
}
