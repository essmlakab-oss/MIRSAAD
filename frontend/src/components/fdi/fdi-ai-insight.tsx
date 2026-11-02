import { useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Bot, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useInterpretation } from "@/hooks/use-xai";
import type { XaiHour } from "@/lib/fdi-api";

/**
 * The executive layer (§2.4, Phase 3 business rewrite): one validated AI turn
 * over the hour's own /api/xai payload — the exact response object, posted
 * back so the server re-validates it before the prompt sees it.
 *
 * Can be rendered with `autoFetch` to trigger immediately on the page (no
 * button), caching per (run_id, station, hour) so navigating between hours
 * does not spend additional tokens. While the request runs the card shows an
 * inline skeleton; on return the strict Phase 3 prose renders in the order a
 * non-technical operator needs:
 *
 *   resume             — one-line executive summary, bold at the top
 *   what_happened      — plain-language narrative
 *   key_factors        — what pushed the verdict, bulleted ("what impacted what")
 *   what_it_means      — business consequence (caveats folded in server-side)
 *   recommended_action — the imperative next step, highlighted
 *
 * Errors: 401 redirects to /auth (the silent refresh already failed), 429 and
 * 502 toast (quota, model), anything else becomes an inline note with a
 * retry — the charts above still stand on their own.
 */

type Props = {
  /** The run the hour belongs to — part of the cache key. */
  runId: string | null;
  /** The hour's /api/xai response; null until it has loaded. */
  payload: XaiHour | null;
  /** If true, skips the button and fetches immediately when the payload is ready. */
  autoFetch?: boolean;
};

export function FDIAiInsight({ runId, payload, autoFetch = false }: Props) {
  const navigate = useNavigate();
  const { data, loading, error, interpret } = useInterpretation(runId);

  const go = () => {
    if (payload) interpret(payload);
  };

  // Auto-fetch if requested by the parent (e.g., the panel layout)
  useEffect(() => {
    if (autoFetch && payload && !data && !loading && !error) {
      interpret(payload);
    }
  }, [autoFetch, payload, data, loading, error, interpret]);

  // 401: the session refresh already failed once inside the client — hand
  // the operator to /auth instead of spinning. 429/502 toast: quota (per
  // user, per hour) and model hiccups are events, not field notes.
  useEffect(() => {
    if (!error) return;
    if (error.status === 401) {
      navigate({ to: "/auth" });
      toast.warning("Your session expired — sign in again.", {
        description: "The interpretation was stopped before anything was quoted.",
      });
    } else if (error.status === 429) {
      toast.warning(error.message, {
        description: "The AI tariff is per user and per hour; try again shortly.",
      });
    } else if (error.status === 502) {
      toast.error(error.message, {
        description: "The model backend hiccuped without leaking unvalidated text.",
      });
    }
  }, [error, navigate]);

  const shownError = !!(
    error &&
    error.status !== 401 &&
    error.status !== 429 &&
    error.status !== 502
  );

  return (
    <div className="space-y-2.5" data-tour="fdi-xai-ai-insight" data-testid="ai-insight">
      {!autoFetch && (
        <div className="flex items-center justify-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={go}
            disabled={!payload || loading}
            aria-disabled={!payload || loading}
            className="gap-2 border-primary/40 bg-primary/5 hover:bg-primary/10"
          >
            <Sparkles className="size-3.5" /> ✨ Interpret with AI
          </Button>
        </div>
      )}

      {loading && (
        <div className="space-y-2 rounded-xl border border-border/50 bg-muted/20 px-4 py-3">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      )}

      {shownError && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-muted-foreground">
          <span>{error.message}</span>
          <button
            type="button"
            onClick={go}
            className="shrink-0 font-medium text-primary underline-offset-4 hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {data && (
        // aria-live: the prose lands seconds after the click and must be
        // announced without stealing focus.
        <div
          aria-live="polite"
          className="rounded-xl border border-blue-500/20 bg-gradient-to-r from-blue-500/10 to-purple-500/10 px-4 py-3"
        >
          <div className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-lg bg-blue-500/15 text-blue-500">
              <Bot className="size-4" />
            </span>
            <div className="leading-tight">
              <p className="text-sm font-semibold tracking-tight">Executive interpretation</p>
              <p className="text-[10px] text-muted-foreground">
                Validated against the panel schema · attempt {data.attempt} · confidence{" "}
                <span className="font-medium" style={{ color: confidenceColor(data.confidence) }}>
                  {data.confidence}
                </span>
              </p>
            </div>
          </div>

          <p className="mt-2 text-sm font-semibold leading-snug text-foreground">{data.resume}</p>

          <section className="mt-2">
            <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              What happened
            </p>
            <p className="text-xs leading-relaxed text-foreground/90">{data.what_happened}</p>
          </section>

          {data.key_factors.length > 0 && (
            <section className="mt-2">
              <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                What impacted what
              </p>
              <ul className="list-inside list-disc space-y-1 text-xs leading-relaxed text-foreground/90">
                {data.key_factors.map((factor) => (
                  <li key={factor}>{factor}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="mt-2">
            <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              What it means
            </p>
            <p className="text-xs leading-relaxed text-foreground/90">{data.what_it_means}</p>
          </section>

          <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-border/40 pt-2">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              Recommended action
            </span>
            <span className="rounded-md border border-blue-500/40 bg-blue-500/10 px-2 py-0.5 text-[11px] font-medium text-blue-600 dark:text-blue-400">
              {data.recommended_action}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function confidenceColor(confidence: "low" | "medium" | "high"): string {
  switch (confidence) {
    case "high":
      return "#10b981";
    case "medium":
      return "#f59e0b";
    default:
      return "#777777";
  }
}
