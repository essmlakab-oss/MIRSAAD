import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArenaDuel } from "@/components/arena/arena-duel";
import { ArenaLauncher } from "@/components/arena/arena-launcher";
import { ArenaDashboard } from "@/components/arena/arena-dashboard";
import {
  arenaApi,
  ArenaError,
  type ArenaFrames,
  type ArenaMeta,
  type ArenaStatus,
} from "@/lib/arena-api";

export const Route = createFileRoute("/_authenticated/dashboard/arena")({
  component: ArenaPage,
});

// ---------------------------------------------------------------------------
// Kind       : Attack Arena page composition
// Description: attacker vs defender. Duel plays one attack as a film (scenario
//              → injection → witnesses → score → decision), Launcher runs many
//              and sorts the misses, Dashboard sums every recorded run.
//              The engine loads lazily on the backend; the page polls
//              /api/arena/status and keeps the actions disabled until ready.
function ArenaPage() {
  const [status, setStatus] = useState<ArenaStatus | null>(null);
  const [meta, setMeta] = useState<ArenaMeta | null>(null);
  const [tab, setTab] = useState("duel");
  const [frames, setFrames] = useState<ArenaFrames | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const s = await arenaApi.status();
        if (cancelled) return;
        setStatus(s);
        if (s.status === "ready") {
          setMeta(await arenaApi.meta());
          return;
        }
        if (s.status !== "unavailable") timer = window.setTimeout(poll, 2500);
      } catch (e) {
        if (!cancelled)
          setStatus({
            status: "unavailable",
            detail: e instanceof ArenaError ? e.message : "The backend did not answer.",
            assets: "",
          });
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  const ready = status?.status === "ready" && meta !== null;

  const onFrames = useCallback((f: ArenaFrames | null) => setFrames(f), []);

  const replay = useCallback(async (jobId: string, attackId: string) => {
    setTab("duel");
    setNotice("Loading the attack (~10 s)…");
    try {
      const F = await arenaApi.replay(jobId, attackId);
      if (F.status === "applied") {
        setFrames(F);
        setNotice(null);
      } else setNotice(`This attack was refused by the simulator: ${F.reason}`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  }, []);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Attack Arena</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Attacker versus defender: inject a false-data attack into a clean 2025 copy of a
            station, watch the detector decide, and sort every miss — detected, as small as natural
            noise, or something to learn from.
          </p>
        </div>
        <EngineChip status={status} />
      </div>

      {status?.status === "unavailable" && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          The arena engine is unavailable: {status.detail}
        </div>
      )}
      {notice && (
        <div className="rounded-xl border border-border/60 bg-muted/60 px-4 py-2 text-sm">
          {notice}
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="duel">Duel</TabsTrigger>
          <TabsTrigger value="launcher">Launcher</TabsTrigger>
          <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
        </TabsList>
        {/* forceMount: the 3D canvas must not be torn down while it initialises (tab switch), and the duel keeps its attack. */}
        <TabsContent value="duel" forceMount className="mt-4 data-[state=inactive]:hidden">
          <ArenaDuel meta={meta} frames={frames} onFrames={onFrames} ready={ready} />
        </TabsContent>
        <TabsContent value="launcher" className="mt-4">
          <ArenaLauncher
            meta={meta}
            ready={ready}
            onReplay={(j, a) => void replay(j, a)}
            onBatchDone={() => setRefreshKey((k) => k + 1)}
          />
        </TabsContent>
        <TabsContent value="dashboard" className="mt-4">
          <ArenaDashboard meta={meta} ready={ready} refreshKey={refreshKey} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function EngineChip({ status }: { status: ArenaStatus | null }) {
  const s = status?.status ?? "loading";
  const style =
    s === "ready"
      ? { label: "Engine ready", color: "#10b981" }
      : s === "unavailable"
        ? { label: "Engine unavailable", color: "#ef4444" }
        : { label: "Engine warming up…", color: "#f59e0b" };
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium"
      style={{
        borderColor: `${style.color}55`,
        color: style.color,
        background: `${style.color}14`,
      }}
    >
      <span className="h-2 w-2 rounded-full" style={{ background: style.color }} />
      {style.label}
    </span>
  );
}
