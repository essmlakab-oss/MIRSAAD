import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FDIDropzone } from "./fdi-dropzone";

/**
 * "Attack classification" entry next to the 24 h evidence strip: a button
 * that opens a plain ingestion modal — the same dropzone the console uses —
 * where the operator supplies the frame they want classified (their own CSV
 * or the demo dataset). Deliberately minimal: one purpose, one surface.
 */
export function FDIIngestButton({
  open,
  onOpenChange,
  onFile,
  onDemo,
  loading,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onFile: (file: File) => void;
  onDemo: () => void;
  loading: boolean;
}) {
  const closeAnd = (action: () => void) => {
    onOpenChange(false);
    action();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onOpenChange(true)}
        data-testid="ingest-button"
        className="gap-1.5"
      >
        <Sparkles className="size-3.5" />
        Attack classification
      </Button>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Classify a new input</DialogTitle>
          <DialogDescription>
            Provide the station-hours you want scored: drop your own CSV or load the sample
            dataset — the detector validates the schema, rebuilds the witnesses and Model 2 names
            the attack family.
          </DialogDescription>
        </DialogHeader>
        <FDIDropzone
          loading={loading}
          onFile={(file) => closeAnd(() => onFile(file))}
          onDemo={() => closeAnd(onDemo)}
        />
      </DialogContent>
    </Dialog>
  );
}
