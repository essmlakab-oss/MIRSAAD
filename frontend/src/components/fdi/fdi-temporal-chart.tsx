import { useMemo } from "react";
import { CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import type { XaiHour } from "@/lib/fdi-api";
import { formatAxisHour, formatMoisture } from "@/lib/fdi";

/**
 * Expected vs actual sm5 for one hour in its ±12 h window (§1.7 temporal):
 * the xai hourly read-out's own pivots. The detector's rule layer compares
 * the trace against its twin — this chart shows that comparison as a shape:
 * expected is what the twin reconstructed from witnesses the attacker does
 * not control; actual is what the station received.
 */

type Props = {
  temporal: XaiHour["temporal"];
  /** The selected hour, Z-suffixed UTC — a reference line marks it. */
  hourIso: string;
};

type Row = {
  t: number;
  tLabel: string;
  actual: number | null;
  expected: number | null;
  selected: boolean;
};

const chartConfig = {
  actual: { label: "Actual (received)", color: "#0ea5e9" },
  expected: { label: "Expected (twin)", color: "#a78bfa" },
} satisfies ChartConfig;

export function FDITemporalChart({ temporal, hourIso }: Props) {
  const rows = useMemo<Row[]>(
    () =>
      temporal.points.map((point) => ({
        t: new Date(point.t).getTime(),
        tLabel: point.t,
        actual: point.observed,
        expected: point.expected,
        selected: point.t === hourIso,
      })),
    [temporal.points, hourIso],
  );

  const selectedPoint = rows.find((row) => row.selected);
  const peak = Math.max(1e-9, ...rows.flatMap((row) => [row.actual ?? 0, row.expected ?? 0]));

  if (rows.length === 0) return <Skeleton className="h-56 w-full" />;

  return (
    <div data-tour="fdi-xai-temporal" className="space-y-2">
      <ChartContainer config={chartConfig} className="h-56 w-full">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="hsl(var(--border))" strokeOpacity={0.4} vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(value: number) => formatAxisHour(new Date(value).toISOString())}
            tickLine={false}
            axisLine={{ stroke: "hsl(var(--border))" }}
            tick={{ fontSize: 10 }}
          />
          <YAxis
            domain={[0, peak * 1.1]}
            tickFormatter={(value: number) => formatMoisture(Number(value))}
            tickLine={false}
            axisLine={{ stroke: "hsl(var(--border))" }}
            tick={{ fontSize: 10 }}
            width={54}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(_, payload) => {
                  const row = payload?.[0]?.payload as Row | undefined;
                  return row ? formatAxisHour(row.tLabel) : "";
                }}
                formatter={(value, name) => [
                  formatMoisture(Number(value)),
                  chartConfig[name as keyof typeof chartConfig]?.label ?? name,
                ]}
              />
            }
          />

          <Line
            name="expected"
            dataKey="expected"
            type="monotone"
            stroke="#a78bfa"
            strokeWidth={1.6}
            strokeDasharray="5 3"
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            name="actual"
            dataKey="actual"
            type="monotone"
            stroke="#0ea5e9"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          {selectedPoint && (
            <ReferenceLine
              x={selectedPoint.t}
              stroke="#ef4444"
              strokeWidth={1.2}
              strokeDasharray="4 3"
            />
          )}
        </ComposedChart>
      </ChartContainer>

      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-1">
            <span className="h-0.5 w-4 rounded" style={{ background: "#0ea5e9" }} /> actual
          </span>
          <span className="inline-flex items-center gap-1">
            <span
              className="h-0.5 w-4 rounded"
              style={{
                background: "#a78bfa",
                backgroundImage:
                  "repeating-linear-gradient(90deg,#a78bfa 0 5px,transparent 5px 8px)",
              }}
            />{" "}
            expected
          </span>
        </div>
        <span>
          {temporal.window_hours} h window · {formatAxisHour(hourIso)}
        </span>
      </div>
    </div>
  );
}
