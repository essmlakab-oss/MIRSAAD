/**
 * The /api/xai explanations for the selected hour.
 *
 * The hour payload is fetched through TanStack Query, keyed by
 * (run_id, station, hour_iso): a run is scored once server-side and never
 * changes after that, so the cache is what makes scrubbing cheap — moving the
 * selection back to a visited hour is a cache hit, not a second detector pass.
 * Errors surface as state, not as thrown exceptions, because the panel is a
 * chart area, not an error boundary.
 *
 * The AI interpretation also caches per (run_id, station, hour). Every call
 * spends a billed token, so clicking Interpret twice on the same hour must
 * never spend it twice.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import {
  fetchXaiHour,
  fetchXaiSeries,
  postInterpretXai,
  FDIError,
  type InterpretXaiRequest,
  type InterpretXaiResponse,
  type XaiHour,
  type XaiSeries,
} from "@/lib/fdi-api";

function messageFor(err: unknown, fallback: string): string {
  return err instanceof FDIError ? err.message : err instanceof Error ? err.message : fallback;
}

/** A 404 on an hour fetch: the run expired, or the hour was filtered out. */
const HOUR_NOT_FOUND =
  "This hour could not be explained. Please re-load the demo or upload the data again.";

/** Map the TanStack query result onto the panel's async-state shape. */
function toState<T>(
  query: UseQueryResult<T, Error>,
  fallbackError: string,
): {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** True when the fetch failed with a 404 — the run expired or the hour dropped. */
  notFound: boolean;
  reload: () => void;
} {
  const notFound = query.isError && query.error instanceof FDIError && query.error.status === 404;
  return {
    data: query.data ?? null,
    loading: query.isPending,
    error: query.isError
      ? notFound
        ? HOUR_NOT_FOUND
        : messageFor(query.error, fallbackError)
      : null,
    notFound,
    reload: () => void query.refetch(),
  };
}

/** The single-hour read-out for the panel, cached per (run_id, station, hour). */
export function useXaiHourState(
  runId: string | null,
  station: string | null,
  hourIso: string | null,
): {
  data: XaiHour | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
  reload: () => void;
} {
  const query = useQuery({
    queryKey: ["xai", "hour", runId, station, hourIso],
    queryFn: () => fetchXaiHour(runId!, station!, hourIso!),
    enabled: !!runId && !!station && !!hourIso,
    // A run's history never changes once scored: no reason to recompute an
    // hour the operator already explained. 30 minutes still lets a manual
    // browser reload pick up a live correction.
    staleTime: 30 * 60_000,
    gcTime: 30 * 60_000,
    retry: false,
  });
  return toState(query, "The rule trace and twin decomposition are unavailable for this hour");
}

/** Cheap per-hour evidence data for the strip, cached per (run_id, station). */
export function useXaiSeriesState(
  runId: string | null,
  station: string | null,
): {
  data: XaiSeries | null;
  loading: boolean;
  error: string | null;
  /** True on a 404 — with the hour fetch also 404ing, this means the run expired. */
  notFound: boolean;
  reload: () => void;
} {
  const query = useQuery({
    queryKey: ["xai", "series", runId, station],
    queryFn: () => fetchXaiSeries(runId!, station!),
    enabled: !!runId && !!station,
    staleTime: 30 * 60_000,
    gcTime: 30 * 60_000,
    retry: false,
  });
  return toState(query, "The evidence strip data is unavailable for this run");
}

/**
 * The AI turn, on demand and cached per (run_id, station, hour).
 *
 * The cache outlives panel mount/unmount (module scope): an operator bouncing
 * between hours does not pay twice for the same prose. Errors carry their
 * HTTP status; the component decides redirect (401), toast (429, 502) or
 * inline note.
 */
const interpretationCache = new Map<string, InterpretXaiResponse>();

export function useInterpretation(runId: string | null): {
  data: InterpretXaiResponse | null;
  loading: boolean;
  error: { message: string; status: number | null } | null;
  interpret: (payload: XaiHour) => void;
  reset: () => void;
} {
  const [state, setState] = useState<{
    data: InterpretXaiResponse | null;
    loading: boolean;
    error: { message: string; status: number | null } | null;
  }>({ data: null, loading: false, error: null });

  const interpret = useCallback(
    (payload: XaiHour) => {
      const cacheKey = `${runId ?? ""}|${payload.meta.station}|${payload.meta.hour_iso}`;

      const cached = interpretationCache.get(cacheKey);
      if (cached) {
        setState({ data: cached, loading: false, error: null });
        return;
      }

      setState({ data: null, loading: true, error: null });

      const body: InterpretXaiRequest = { payload };
      postInterpretXai(body)
        .then((data) => {
          interpretationCache.set(cacheKey, data);
          setState({ data, loading: false, error: null });
        })
        .catch((err) => {
          setState({
            data: null,
            loading: false,
            error: {
              message: messageFor(err, "The AI interpretation is unavailable"),
              status: err instanceof FDIError ? err.status : null,
            },
          });
        });
    },
    [runId],
  );

  const reset = useCallback(() => setState({ data: null, loading: false, error: null }), []);

  return { ...state, interpret, reset };
}
