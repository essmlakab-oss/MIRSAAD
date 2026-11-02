import { useCallback, useRef, useState } from "react";
import { Loader2, Sparkles, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FDI_SCHEMA_HINT } from "@/lib/fdi";

/**
 * The ingestion surface: drop a CSV, or load the demo dataset.
 *
 * The whole dashed zone is one click target — an operator's first instinct is
 * to click the picture, so "click to browse" is the dropzone itself and no
 * separate button teaches them the hard way. It also states the schema
 * *before* the upload, so a rejected file is not the first thing that teaches
 * the contract. And it shows progress while the backend scores — which is not
 * instantaneous: a 34-day demo is ~1,600 station-hours through a two-stage
 * detector.
 */

type Props = {
  onFile: (file: File) => void;
  onDemo: () => void;
  loading: boolean;
};

export function FDIDropzone({ onFile, onDemo, loading }: Props) {
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const take = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (file && !loading) onFile(file);
    },
    [loading, onFile],
  );

  return (
    <div className="space-y-2" data-tour="fdi-dropzone">
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="sr-only"
        onChange={(event) => {
          take(event.target.files);
          // Let the same file be re-dropped after a rejection.
          event.target.value = "";
        }}
      />

      {loading ? (
        <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-white/25 bg-white/5 px-4 py-6 text-center">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          <p className="text-xs text-muted-foreground">
            Scoring station-hours — the detector runs a digital twin plus three physical rules
          </p>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setOver(false);
            take(event.dataTransfer.files);
          }}
          className={`w-full rounded-2xl border border-dashed px-4 py-8 text-center transition-all ${
            over
              ? "border-primary bg-primary/10"
              : "border-white/25 bg-white/5 hover:border-white/40 hover:bg-white/10"
          }`}
        >
          <UploadCloud className="mx-auto h-7 w-7 text-current opacity-70" />
          <p className="mt-2 text-sm font-semibold tracking-tight">Drag &amp; drop telemetry CSV</p>
          <p className="mt-0.5 text-xs opacity-70">or click to browse</p>
        </button>
      )}

      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onDemo}
        disabled={loading}
        className="w-full"
      >
        <Sparkles className="mr-1.5 h-3.5 w-3.5" />
        Load Demo Dataset
      </Button>

      {/* The full contract, on hover: stated before the upload, not after a
          rejection is the first thing that teaches it. */}
      <p className="text-[10px] leading-snug opacity-60" title={FDI_SCHEMA_HINT}>
        Hourly station-hours · raw USCRN / ERA5 column names accepted · about 18 days of history
        needed
      </p>
    </div>
  );
}
