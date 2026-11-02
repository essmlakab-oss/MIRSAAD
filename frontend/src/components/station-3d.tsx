import { Suspense, lazy, useEffect, useState } from "react";
import type { FDILevel } from "@/lib/fdi";

const StationScene = lazy(() => import("@/components/station-scene"));

/**
 * The FDI alarm the scene has to express: the beacon beside the mast turns
 * red and flashes while the selected hour is a confirmed attack.
 */
export type StationAlarm = {
  color: string;
  label: string;
  detail: string;
};

/**
 * One of the three fixed ground nodes the Data Inspector draws.
 */
export type InspectorNode = {
  /** The sensor this node stands for, e.g. "sm5". Also its scene key. */
  sensor: string;
  /** Shown above the reading: "Target", "Witness", "Weather". */
  role: string;
  value: number | null;
  unit: string;
  /** Digits after the point — soil probes read 4, rain 2. */
  decimals: number;
  /**
   * Optional (Attack Arena): "faked" pulses the node in the attacker's orange,
   * "witness" paints it green as a sensor the attacker cannot reach.
   */
  state?: "faked" | "witness";
};

/**
 * Optional rain falling over the station (Attack Arena). `fake` = rain the
 * attacker invented, `hidden` = real rain the station no longer reports.
 */
export type StationRain = { mm: number; kind: "real" | "fake" | "hidden" };

/**
 * What the Data Inspector shows for the hour currently selected on the
 * timeline. `hour: null` is the dormant state.
 */
export type StationInspector = {
  /** Unique ID for this station (e.g., the station name) */
  id: string;
  /** Short hour label, e.g. "29 Mar 00:00"; null while dormant. */
  hour: string | null;
  /** Level of the selected hour, which drives the attack glitch. */
  level: FDILevel | null;
  /** The level's colour for the Target beacon; null while dormant. */
  color: string | null;
  nodes: InspectorNode[];
  /** Optional rain shown over the station; absent = no rain drawn. */
  rain?: StationRain | null;
};

type Station3DProps = {
  /** The primary station being interacted with in the UI. */
  inspector: StationInspector;
  /**
   * An optional array of ALL stations in the dataset. If provided, the scene
   * will map them out across the farm. The active `inspector` will be highlighted.
   */
  stations?: StationInspector[];
  overlay?: React.ReactNode;
  alarm?: StationAlarm | null;
};

function Overlay({ children }: { children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl bg-background/70 text-sm text-muted-foreground backdrop-blur-[1px]">
      {children}
    </div>
  );
}

export function Station3D({ inspector, stations, overlay, alarm = null }: Station3DProps) {
  // `mounted` gates the canvas on the client: a WebGL context cannot be created
  // during the server pass, and hydrating one that never existed throws.
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  // If no array of stations is provided, we just render the single inspector as one station.
  const stationsToRender = stations && stations.length > 0 ? stations : [inspector];

  return (
    <div data-tour="station-3d" className="relative h-[26rem] w-full overflow-hidden rounded-xl">
      {mounted && (
        <Suspense fallback={null}>
          <StationScene stations={stationsToRender} activeStationId={inspector.id} alarm={alarm} />
        </Suspense>
      )}

      {!mounted && <Overlay>Loading data inspector…</Overlay>}

      {/* The slot sits above the canvas: an interactive panel needs pointer events */}
      {mounted && overlay}
    </div>
  );
}
