/**
 * The current FDI run and the hour selected within it.
 *
 * A run is created once (upload or demo) and then drives the whole page: the
 * evidence strip lays its hours out as evidence, the control center summarizes
 * it, and every XAI request names its `run_id` plus the selected `row`.
 *
 * The selection defaults to the first *explainable* candidate — the backend
 * ranks ATTAQUE > SUSPECT > normal and already excludes "historique
 * insuffisant" hours, whose NaN score leaves nothing to explain.
 */

import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { FDIError, postDemo, postPredict } from "@/lib/fdi-api";
import type { FDIDemoResponse, FDIPredictResponse } from "@/lib/fdi";
import { isExplainable } from "@/lib/fdi";

export type FDISource = "upload" | "demo";

type State = {
  run: FDIPredictResponse | null;
  demo: FDIDemoResponse | null;
  /** Index of the selected hour within run.points. */
  selectedRow: number | null;
  loading: boolean;
  error: string | null;
  loadDemo: () => Promise<void>;
  loadFile: (file: File) => Promise<void>;
  select: (row: number) => void;
  clear: () => void;
};

export function useFDIRun(): State {
  const [run, setRun] = useState<FDIPredictResponse | null>(null);
  const [demo, setDemo] = useState<FDIDemoResponse | null>(null);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const adopt = useCallback((next: FDIPredictResponse, isDemo: FDIDemoResponse | null) => {
    setRun(next);
    setDemo(isDemo);

    // First explainable candidate: the backend has already ranked attack >
    // suspect > normal and dropped the unscorable hours.
    const first = next.candidates.find((candidate) => isExplainable(candidate)) ?? null;
    setSelectedRow(first ? first.i : (next.points[0]?.i ?? null));
  }, []);

  const loadDemo = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await postDemo();
      adopt(response, response);
      toast.success(
        `Demo dataset scored — ${response.summary.attaque} attacks across ${response.summary.hours} hours`,
      );
    } catch (err) {
      const message = err instanceof FDIError ? err.message : "Could not load the demo dataset";
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [adopt]);

  const loadFile = useCallback(
    async (file: File) => {
      setLoading(true);
      setError(null);
      try {
        const response = await postPredict(file);
        adopt(response, null);
        toast.success(
          `${file.name} scored — ${response.summary.attaque} attacks across ${response.summary.hours} hours`,
        );
      } catch (err) {
        const message = err instanceof FDIError ? err.message : "Could not score that file";
        setError(message);
        toast.error(message);
      } finally {
        setLoading(false);
      }
    },
    [adopt],
  );

  const select = useCallback((row: number) => setSelectedRow(row), []);
  const clear = useCallback(() => {
    setRun(null);
    setDemo(null);
    setSelectedRow(null);
    setError(null);
  }, []);

  // A run expiring server-side invalidates the selection with it, so the
  // evidence strip can never point at a row the explanations will 404 on.
  return useMemo(
    () => ({
      run,
      demo,
      selectedRow,
      loading,
      error,
      loadDemo,
      loadFile,
      select,
      clear,
    }),
    [run, demo, selectedRow, loading, error, loadDemo, loadFile, select, clear],
  );
}

/**
 * Whether the page should ask for explanations at all.
 *
 * Split out because the timeline, the control center and the XAI panel all
 * need to agree on it, and "we have a run and a selected hour" is the single
 * condition they share.
 */
export function useSelection(state: State) {
  const point = useMemo(() => {
    if (!state.run || state.selectedRow === null) return null;
    return state.run.points.find((p) => p.i === state.selectedRow) ?? null;
  }, [state.run, state.selectedRow]);

  const explainable = point ? isExplainable(point) : false;

  return { point, explainable };
}
