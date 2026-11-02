import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  arenaApi,
  ARENA_COLORS,
  BUCKET_STYLE,
  FAMILY_LABEL,
  type ArenaFrontier,
  type ArenaMeta,
  type ArenaScoreboard,
} from "@/lib/arena-api";

/**
 * Dashboard — every attack the arena has run: how many were caught, per
 * family, how the misses are sorted (known limit / train / reference) and how
 * detection grows with the size of the attack.
 */

const card = "rounded-2xl border border-border/60 bg-card p-4";
const shadow = { boxShadow: "var(--shadow-elegant)" };

export function ArenaDashboard({
  meta,
  ready,
  refreshKey,
}: {
  meta: ArenaMeta | null;
  ready: boolean;
  refreshKey: number;
}) {
  const [sb, setSb] = useState<ArenaScoreboard | null>(null);
  const [fr, setFr] = useState<ArenaFrontier | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    Promise.all([arenaApi.scoreboard(), arenaApi.frontier()])
      .then(([a, b]) => {
        setSb(a);
        setFr(b);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [ready, refreshKey]);

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!sb || !fr)
    return (
      <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-2xl" />
        ))}
      </div>
    );

  const T = sb.totals;
  const missed = (T.attempts ?? 0) - (T.detected ?? 0);
  const kpis = [
    { k: "Attacks run", v: T.attempts ?? 0, s: "in 2025, the unseen year" },
    {
      k: "Detected",
      v: `${Math.round((T.rate ?? 0) * 100)} %`,
      s: `${T.detected ?? 0} attacks`,
      c: ARENA_COLORS.detected,
    },
    { k: "Got through", v: missed, s: "logged as weaknesses", c: ARENA_COLORS.missed },
    { k: "Known limits", v: T.known_limit ?? 0, s: "as small as natural noise" },
    { k: "Train", v: T.train ?? 0, s: "to learn from (70 %)" },
    { k: "Reference", v: T.reference ?? 0, s: "frozen to measure progress (30 %)" },
  ];
  const buckets = [
    ["detected", T.detected ?? 0],
    ["known_limit", T.known_limit ?? 0],
    ["train", T.train ?? 0],
    ["reference", T.reference ?? 0],
  ] as const;
  const total = Math.max(
    1,
    buckets.reduce((a, [, n]) => a + n, 0),
  );

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {kpis.map((x) => (
          <div key={x.k} className={card} style={shadow}>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {x.k}
            </div>
            <div
              className="mt-1 text-2xl font-semibold tracking-tight"
              style={x.c ? { color: x.c } : undefined}
            >
              {x.v}
            </div>
            <div className="text-[11px] text-muted-foreground">{x.s}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <section className={card} style={shadow}>
          <h2 className="mb-2 text-sm font-semibold tracking-tight">Scoreboard by attack type</h2>
          <table className="w-full text-xs">
            <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="py-1.5 text-left font-semibold">Type</th>
                <th className="py-1.5 text-right font-semibold">Attempts</th>
                <th className="py-1.5 pl-3 text-left font-semibold">Detected</th>
                <th className="py-1.5 text-right font-semibold">Got through</th>
                <th className="py-1.5 text-right font-semibold">Max damage unseen</th>
                <th className="py-1.5 text-right font-semibold">Mean delay</th>
              </tr>
            </thead>
            <tbody>
              {sb.families.map((f) => (
                <tr key={f.family} className="border-t border-border/50">
                  <td className="py-1.5">{f.label}</td>
                  <td className="py-1.5 text-right font-mono">{f.attempts}</td>
                  <td className="py-1.5 pl-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-primary"
                          style={{ width: `${f.rate * 100}%` }}
                        />
                      </div>
                      <span className="font-mono">{Math.round(f.rate * 100)} %</span>
                    </div>
                  </td>
                  <td className="py-1.5 text-right font-mono">{f.got_through}</td>
                  <td className="py-1.5 text-right font-mono">
                    {f.max_undetected_damage === null ? "–" : f.max_undetected_damage.toFixed(3)}
                  </td>
                  <td className="py-1.5 text-right font-mono">
                    {f.mean_delay_h === null
                      ? "–"
                      : f.mean_delay_h <= 0
                        ? "at start"
                        : `+${Math.round(f.mean_delay_h)} h`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className={card} style={shadow}>
          <h2 className="mb-3 text-sm font-semibold tracking-tight">Where the missed attacks go</h2>
          <div className="flex h-3 overflow-hidden rounded-full">
            {buckets.map(([k, n]) => (
              <div
                key={k}
                style={{ width: `${(100 * n) / total}%`, background: BUCKET_STYLE[k].color }}
                title={`${BUCKET_STYLE[k].label}: ${n}`}
              />
            ))}
          </div>
          <ul className="mt-3 space-y-1.5 text-xs">
            {buckets.map(([k, n]) => (
              <li key={k} className="flex items-start gap-2">
                <span
                  className="mt-1 h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ background: BUCKET_STYLE[k].color }}
                />
                <span>
                  <b>{n}</b> {BUCKET_STYLE[k].label} —{" "}
                  <span className="text-muted-foreground">{BUCKET_STYLE[k].hint}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-[11px] leading-snug text-muted-foreground">
            Split by attack group (type, sensors, shape, duration, size, station), never by hour, so
            variants of one attack never land on both sides. A new model is accepted only if
            Reference improves without losing on the 2025 test, the cyber test or false alarms.
          </p>
        </section>
      </div>

      <DetectionHeatmap fr={fr} />

      {meta && (
        <section className={card} style={shadow}>
          <h2 className="mb-1 text-sm font-semibold tracking-tight">The defender</h2>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {meta.model.name}: two LightGBM stages on ~200 physical witnesses, digital twins built
            from the sensors the attacker cannot reach. Tested on 2025, never used for training:{" "}
            <b className="text-foreground">F1 {meta.model.f1_cyber.toFixed(3)}</b> on the cyber
            team's official release,{" "}
            <b className="text-foreground">F1 {meta.model.f1_ours.toFixed(2)}</b> on our
            multi-sensor attacks, precision {meta.model.precision.toFixed(2)}, false alarms{" "}
            {(meta.model.false_alarms * 100).toFixed(1)} % of normal hours. Alert threshold{" "}
            {(meta.threshold * 100).toFixed(1)} %.
          </p>
        </section>
      )}
    </div>
  );
}

/**
 * Attack type × attack size, coloured by the share detected (one hue, light →
 * dark). Reads as "from what size each attack gets caught". Sizes are the
 * largest change of the 5 cm probe; cells with no attack stay empty.
 */
function DetectionHeatmap({ fr }: { fr: ArenaFrontier }) {
  const [hover, setHover] = useState<{ fam: string; size: string } | null>(null);
  const cols = new Map<string, number>();
  Object.values(fr).forEach((pts) => pts.forEach((p) => cols.set(p.size, p.mid)));
  const sizes = [...cols.entries()].sort((a, b) => a[1] - b[1]).map(([s]) => s);
  const fams = Object.keys(FAMILY_LABEL).filter((f) => fr[f]?.length);
  const cell = (f: string, s: string) => fr[f]?.find((p) => p.size === s) ?? null;
  const head = (s: string) => {
    const [lo, hi] = s.split("–").map(Number);
    return hi >= 1 ? `> ${lo.toFixed(2)}` : `${lo.toFixed(3)}–${hi.toFixed(3)}`;
  };
  const H = hover ? cell(hover.fam, hover.size) : null;

  return (
    <section className={card} style={shadow}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-tight">
          From what size each attack gets caught
        </h2>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span>detected</span>
          <span>0 %</span>
          <span
            className="h-2.5 w-24 rounded-sm"
            style={{
              background:
                "linear-gradient(to right, color-mix(in oklab, var(--primary) 6%, transparent), var(--primary))",
            }}
          />
          <span>100 %</span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-[3px] text-xs">
          <thead>
            <tr className="text-[10px] text-muted-foreground">
              <th className="pr-2 text-left font-semibold uppercase tracking-wider">Type</th>
              {sizes.map((s) => (
                <th key={s} className="px-1 text-center font-mono font-normal">
                  {head(s)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fams.map((f) => (
              <tr key={f}>
                <td className="whitespace-nowrap pr-2">{FAMILY_LABEL[f]}</td>
                {sizes.map((s) => {
                  const c = cell(f, s);
                  const on = hover?.fam === f && hover.size === s;
                  return (
                    <td
                      key={s}
                      className={cn(
                        "h-9 min-w-[3.25rem] rounded-md text-center font-mono transition-shadow",
                        on && "ring-2 ring-foreground/60",
                      )}
                      style={
                        c
                          ? {
                              background: `color-mix(in oklab, var(--primary) ${Math.round(6 + 94 * c.rate)}%, transparent)`,
                              color: c.rate > 0.55 ? "var(--primary-foreground)" : undefined,
                            }
                          : { border: "1px dashed var(--border)" }
                      }
                      onMouseEnter={() => setHover({ fam: f, size: s })}
                      onMouseLeave={() => setHover(null)}
                    >
                      {c ? `${Math.round(c.rate * 100)}%` : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 min-h-[1rem] text-[11px] text-muted-foreground">
        {hover && H
          ? `${FAMILY_LABEL[hover.fam]}, size ${head(hover.size)} m³/m³: ${Math.round(H.rate * H.n)} of ${H.n} attacks detected (${Math.round(H.rate * 100)} %).`
          : hover
            ? `${FAMILY_LABEL[hover.fam]}, size ${head(hover.size)}: no attack of this size run yet.`
            : "Size = largest change of the 5 cm probe (m³/m³). Hover a cell for the count; empty = not run yet."}
      </p>
    </section>
  );
}
