/**
 * What the copilot needs to know about the point on screen.
 *
 * The assistant already reads the loaded run server-side (app/fdi/store.py),
 * but "what is the score of this hour?" is about the *selection*, which lives
 * here in the browser. The FDI page writes it as the operator scrubs the
 * timeline; the copilot bubble names it in the request body, so the answer can
 * be about the exact hour they are looking at rather than the run as a whole.
 *
 * One store, two writers is a smell — but there is only one writer (the page)
 * and one reader (the bubble), and they are never mounted apart, so the
 * alternative is threading the selection through a context both already
 * share a parent for.
 */

import { create } from "zustand";

type FDIContextState = {
  /** The run the FDI page is showing, or null when nothing is loaded. */
  runId: string | null;
  /** Index of the selected hour within run.points, or null before a pick. */
  row: number | null;
  set: (next: Partial<Pick<FDIContextState, "runId" | "row">>) => void;
};

export const useFDIContext = create<FDIContextState>()((set) => ({
  runId: null,
  row: null,
  set: (next) => set(next),
}));
