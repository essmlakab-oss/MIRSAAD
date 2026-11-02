import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Html, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { useTheme } from "@/components/theme-provider";
import type {
  InspectorNode,
  StationAlarm,
  StationInspector,
  StationRain,
} from "@/components/station-3d";

// ---------------------------------------------------------------------------
// Layout & Business Metaphor
// ---------------------------------------------------------------------------

const FARM_URL = "/models/farm.glb";
useGLTF.preload(FARM_URL);

const CORE_HEIGHT = 5.0;
const PULSE_TRAVEL = 1.15;
const WITNESS_COLOR = "#38bdf8";
const TWIN_COLOR = "#a78bfa";
const DORMANT_COLOR = "#64748b";
const FAKED_COLOR = "#f97316";
const UNTOUCHED_COLOR = "#10b981";

/**
 * Station placements across the farm.
 * Station 0 maps exactly to the red circle mark (open field below the house).
 */
const STATION_COORDS: [number, number, number][] = [
  [10, 0, 15], // Red circle location (Primary)
  [-18, 0, -18], // Far top-left (Secondary, if needed)
  [22, 0, -12], // Right side
  [-12, 0, 18], // Bottom left
];

function approach(current: number, target: number, delta: number, rate = 1.6): number {
  return current + (target - current) * Math.min(1, delta * rate);
}

// ---------------------------------------------------------------------------
// The Farm (Base Environment)
// ---------------------------------------------------------------------------

function FarmEnvironment() {
  const { scene } = useGLTF(FARM_URL);
  const clonedScene = useMemo(() => scene.clone(true), [scene]);

  return <primitive object={clonedScene} scale={1} position={[0, 0, 0]} receiveShadow />;
}

// ---------------------------------------------------------------------------
// The Digital Twin Core (AI Hub)
// ---------------------------------------------------------------------------

function TwinCore({ alarm, active }: { alarm: StationAlarm | null; active: boolean }) {
  const meshRef = useRef<THREE.Mesh>(null);
  const materialRef = useRef<THREE.MeshStandardMaterial>(null);
  const ringRef = useRef<THREE.Mesh>(null);
  const groupRef = useRef<THREE.Group>(null);

  const isAlert = alarm !== null;
  const scale = useRef(0);

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;

    scale.current = approach(scale.current, active ? 1 : 0, delta, 3);
    if (groupRef.current) groupRef.current.scale.setScalar(scale.current);

    if (meshRef.current) {
      meshRef.current.rotation.y = t * 0.2;
      meshRef.current.rotation.x = Math.sin(t * 0.5) * 0.2;
      meshRef.current.position.y = CORE_HEIGHT + Math.sin(t * 1.5) * 0.2;

      if (isAlert) {
        meshRef.current.position.x = (Math.random() - 0.5) * 0.3;
        meshRef.current.position.z = (Math.random() - 0.5) * 0.3;
      } else {
        meshRef.current.position.x = approach(meshRef.current.position.x, 0, delta);
        meshRef.current.position.z = approach(meshRef.current.position.z, 0, delta);
      }
    }

    if (materialRef.current) {
      materialRef.current.emissiveIntensity = isAlert
        ? 2.5 + Math.random() * 2
        : 1.0 + Math.sin(t * 2) * 0.3;
    }

    if (ringRef.current) {
      ringRef.current.rotation.x = Math.PI / 2 + Math.sin(t * 0.8) * 0.1;
      ringRef.current.rotation.z = t * 0.4;
    }
  });

  if (!active && scale.current < 0.01) return null;

  return (
    <group ref={groupRef}>
      <mesh ref={meshRef} position={[0, CORE_HEIGHT, 0]} castShadow>
        <octahedronGeometry args={[1.2, 0]} />
        <meshStandardMaterial
          ref={materialRef}
          color={isAlert ? "#ef4444" : TWIN_COLOR}
          emissive={isAlert ? "#ef4444" : TWIN_COLOR}
          wireframe
          toneMapped={false}
        />
      </mesh>

      <mesh ref={ringRef} position={[0, CORE_HEIGHT, 0]}>
        <torusGeometry args={[2.0, 0.05, 8, 32]} />
        <meshBasicMaterial color={isAlert ? "#ef4444" : TWIN_COLOR} transparent opacity={0.5} />
      </mesh>

      <mesh position={[0, CORE_HEIGHT / 2, 0]}>
        <cylinderGeometry args={[0.05, 0.05, CORE_HEIGHT, 4]} />
        <meshBasicMaterial color={TWIN_COLOR} transparent opacity={0.3} />
      </mesh>

      <Html
        position={[0, CORE_HEIGHT + 1.5, 0]}
        center
        distanceFactor={25}
        style={{ pointerEvents: "none" }}
      >
        <div className="whitespace-nowrap rounded-md border border-violet-500/50 bg-violet-500/20 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-violet-300 backdrop-blur-md">
          Digital Twin Core
        </div>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------------------
// IoT Sensors
// ---------------------------------------------------------------------------

function DataNode({
  index,
  count,
  node,
  dormant,
  levelColor,
  level,
  hour,
}: {
  index: number;
  count: number;
  node: InspectorNode;
  dormant: boolean;
  levelColor: string | null;
  level: StationInspector["level"];
  hour: string | null;
}) {
  const isTarget = index === 0;
  const glitching = isTarget && level === "ATTAQUE";
  const isWeather = index === 2;

  const beaconColor = dormant
    ? DORMANT_COLOR
    : isTarget
      ? (levelColor ?? DORMANT_COLOR)
      : node.state === "faked"
        ? FAKED_COLOR
        : node.state === "witness"
          ? UNTOUCHED_COLOR
          : isWeather
            ? "#fbbf24"
            : WITNESS_COLOR;

  const groupRef = useRef<THREE.Group>(null);
  const beaconRef = useRef<THREE.MeshStandardMaterial>(null);
  const anemometerRef = useRef<THREE.Group>(null);

  const glitch = useRef(0);
  const stepAt = useRef(0);
  const scale = useRef(0);

  // Spread the nodes in a triangle around the core
  const slotRadius = 4.5;
  const angle = (index * Math.PI * 2) / count;
  const slotX = Math.cos(angle) * slotRadius;
  const slotZ = Math.sin(angle) * slotRadius;

  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;

    scale.current = approach(scale.current, dormant ? 0 : 1, delta, 3);
    glitch.current = approach(glitch.current, glitching ? 1 : 0, delta, 2.2);

    if (groupRef.current) {
      groupRef.current.scale.setScalar(scale.current);

      if (glitch.current > 0.004 && t - stepAt.current > 0.07) {
        stepAt.current = t;
        const amp = 0.2 * glitch.current;
        groupRef.current.position.set(
          slotX + (Math.random() - 0.5) * amp,
          0,
          slotZ + (Math.random() - 0.5) * amp,
        );
      } else {
        groupRef.current.position.set(slotX, 0, slotZ);
      }
    }

    if (isWeather && anemometerRef.current) {
      anemometerRef.current.rotation.y = t * 3;
    }

    let emissive = dormant ? 0.12 : 1.0 + Math.sin(t * 1.8) * 0.35;
    if (glitch.current > 0.004) {
      emissive = emissive * (1 - glitch.current) + (0.35 + Math.random() * 2.4) * glitch.current;
    }

    if (beaconRef.current) beaconRef.current.emissiveIntensity = emissive;
  });

  if (dormant && scale.current < 0.01) return null;

  const valueText = node.value === null ? "—" : `${node.value.toFixed(node.decimals)} ${node.unit}`;
  const border = dormant ? "rgba(120,120,120,.4)" : beaconColor;

  const mastHeight = isWeather ? 3.0 : isTarget ? 1.5 : 2.2;
  const beaconHeight = mastHeight + 0.15;

  return (
    <group ref={groupRef} position={[slotX, 0, slotZ]}>
      {/* Base */}
      <mesh receiveShadow position={[0, 0.1, 0]}>
        <boxGeometry args={[1.2, 0.2, 1.2]} />
        <meshStandardMaterial color={dormant ? "#414a58" : "#4b5563"} roughness={0.8} />
      </mesh>

      {/* Mast */}
      <mesh castShadow position={[0, mastHeight / 2, 0]}>
        <boxGeometry args={[0.15, mastHeight, 0.15]} />
        <meshStandardMaterial color="#6b7280" roughness={0.6} metalness={0.4} />
      </mesh>

      {/* Weather Anemometer */}
      {isWeather && (
        <group ref={anemometerRef} position={[0, mastHeight - 0.2, 0]}>
          <mesh position={[0.25, 0, 0]}>
            <boxGeometry args={[0.1, 0.1, 0.1]} />
            <meshBasicMaterial color="#fbbf24" />
          </mesh>
          <mesh position={[-0.25, 0, 0]}>
            <boxGeometry args={[0.1, 0.1, 0.1]} />
            <meshBasicMaterial color="#fbbf24" />
          </mesh>
          <mesh position={[0, 0, 0.25]}>
            <boxGeometry args={[0.1, 0.1, 0.1]} />
            <meshBasicMaterial color="#fbbf24" />
          </mesh>
          <mesh position={[0, 0, -0.25]}>
            <boxGeometry args={[0.1, 0.1, 0.1]} />
            <meshBasicMaterial color="#fbbf24" />
          </mesh>
        </group>
      )}

      {node.state === "faked" && !dormant && <FakeRing />}

      {/* Beacon */}
      <mesh position={[0, beaconHeight, 0]}>
        <boxGeometry args={[0.35, 0.35, 0.35]} />
        <meshStandardMaterial
          ref={beaconRef}
          color={beaconColor}
          emissive={beaconColor}
          emissiveIntensity={1}
          toneMapped={false}
        />
      </mesh>

      {/* Labels */}
      <Html
        position={[0, beaconHeight + 0.6, 0]}
        center
        distanceFactor={22}
        style={{ pointerEvents: "none" }}
      >
        <div
          className="whitespace-nowrap rounded-md border px-2 py-1 text-center backdrop-blur-md shadow-lg"
          style={{
            borderColor: border,
            background: "rgba(10,12,16,.85)",
            color: "#f8fafc",
          }}
        >
          <div className="text-[8px] font-semibold leading-none text-muted-foreground uppercase tracking-wider">
            {node.role}
          </div>
          <div className="mt-0.5 text-[11px] font-bold leading-none">{node.sensor}</div>
          <div className="mt-1 text-[10px] font-medium leading-none" style={{ color: beaconColor }}>
            {dormant ? "—" : valueText}
          </div>
        </div>
      </Html>
    </group>
  );
}

/** Orange ring spreading from the base of a sensor the attacker is rewriting. */
function FakeRing() {
  const ref = useRef<THREE.Mesh>(null);
  const mat = useRef<THREE.MeshBasicMaterial>(null);
  useFrame((state) => {
    const k = (state.clock.elapsedTime % 1.4) / 1.4;
    if (ref.current) ref.current.scale.setScalar(0.6 + k * 1.8);
    if (mat.current) mat.current.opacity = 0.75 * (1 - k);
  });
  return (
    <mesh ref={ref} position={[0, 0.25, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[0.7, 0.85, 40]} />
      <meshBasicMaterial
        ref={mat}
        color={FAKED_COLOR}
        transparent
        toneMapped={false}
        side={THREE.DoubleSide}
      />
    </mesh>
  );
}

// ---------------------------------------------------------------------------
// Rain over the station (Attack Arena): real, faked or hidden
// ---------------------------------------------------------------------------

const RAIN_TOP = 11;

function RainField({ rain }: { rain: StationRain }) {
  const count = Math.min(700, Math.round(120 + rain.mm * 70));
  const ref = useRef<THREE.LineSegments>(null);
  const positions = useMemo(() => {
    const a = new Float32Array(count * 6);
    for (let i = 0; i < count; i++) {
      const x = (Math.random() - 0.5) * 13;
      const z = (Math.random() - 0.5) * 13;
      const y = Math.random() * RAIN_TOP;
      a.set([x, y, z, x, y + 0.45, z], i * 6);
    }
    return a;
  }, [count]);

  useFrame((_state, delta) => {
    const geo = ref.current?.geometry;
    if (!geo) return;
    const arr = geo.attributes.position.array as Float32Array;
    const dy = Math.min(delta, 0.05) * 13;
    for (let i = 0; i < count; i++) {
      let y = arr[i * 6 + 1] - dy;
      if (y < 0) y += RAIN_TOP;
      arr[i * 6 + 1] = y;
      arr[i * 6 + 4] = y + 0.45;
    }
    geo.attributes.position.needsUpdate = true;
  });

  const color = rain.kind === "fake" ? FAKED_COLOR : "#60a5fa";
  const label =
    rain.kind === "fake"
      ? `Fake rain · ${rain.mm.toFixed(1)} mm/h`
      : rain.kind === "hidden"
        ? `Real rain, not reported · ${rain.mm.toFixed(1)} mm/h`
        : `Rain · ${rain.mm.toFixed(1)} mm/h`;

  return (
    <group>
      <lineSegments ref={ref}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        </bufferGeometry>
        <lineBasicMaterial
          color={color}
          transparent
          opacity={rain.kind === "hidden" ? 0.3 : 0.75}
          depthWrite={false}
          toneMapped={false}
        />
      </lineSegments>
      <Html
        position={[0, RAIN_TOP + 0.8, 0]}
        center
        distanceFactor={25}
        style={{ pointerEvents: "none" }}
      >
        <div
          className="whitespace-nowrap rounded-md border px-2 py-0.5 text-[10px] font-semibold"
          style={{ borderColor: color, color, background: "rgba(10,12,16,.8)" }}
        >
          {label}
        </div>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Data Pulses (Physics -> Twin)
// ---------------------------------------------------------------------------

type Pulse = { id: number; from: THREE.Vector3; to: THREE.Vector3; color: string; born: number };

function Pulses({ pulses, now }: { pulses: Pulse[]; now: () => number }) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame(() => {
    const group = groupRef.current;
    if (!group) return;

    const t = now();
    group.children.forEach((child, index) => {
      const pulse = pulses[index];
      if (!pulse) return;

      const progress = Math.min((t - pulse.born) / PULSE_TRAVEL, 1);
      const control = pulse.from
        .clone()
        .lerp(pulse.to, 0.5)
        .setY(pulse.to.y + 2);
      const inv = 1 - progress;

      child.position
        .copy(pulse.from)
        .multiplyScalar(inv * inv)
        .addScaledVector(control, 2 * inv * progress)
        .addScaledVector(pulse.to, progress * progress);

      const scale = Math.sin(progress * Math.PI) * 1.0 + 0.2;
      child.scale.setScalar(scale);
    });
  });

  return (
    <group ref={groupRef}>
      {pulses.map((pulse) => (
        <mesh key={pulse.id}>
          <boxGeometry args={[0.15, 0.15, 0.15]} />
          <meshBasicMaterial color={pulse.color} toneMapped={false} transparent opacity={0.9} />
        </mesh>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Scene Layout & Logic
// ---------------------------------------------------------------------------

function StationCluster({
  station,
  alarm,
  position,
}: {
  station: StationInspector;
  alarm: StationAlarm | null;
  position: [number, number, number];
}) {
  const dormant = station.hour === null;
  const [pulses, setPulses] = useState<Pulse[]>([]);
  const clockRef = useRef(0);
  const pulseId = useRef(0);

  useFrame((state) => {
    clockRef.current = state.clock.elapsedTime;
    setPulses((current) =>
      current.some((pulse) => state.clock.elapsedTime - pulse.born > PULSE_TRAVEL)
        ? current.filter((pulse) => state.clock.elapsedTime - pulse.born <= PULSE_TRAVEL)
        : current,
    );
  });

  useEffect(() => {
    if (station.hour === null) {
      setPulses([]);
      return;
    }

    const born = clockRef.current;
    const slotRadius = 4.5;

    setPulses(
      station.nodes.map((node, index) => {
        const angle = (index * Math.PI * 2) / station.nodes.length;
        const slotX = Math.cos(angle) * slotRadius;
        const slotZ = Math.sin(angle) * slotRadius;

        return {
          id: pulseId.current++,
          from: new THREE.Vector3(slotX, 2.0, slotZ),
          to: new THREE.Vector3(0, CORE_HEIGHT, 0),
          color:
            index === 0
              ? (station.color ?? DORMANT_COLOR)
              : node.state === "faked"
                ? FAKED_COLOR
                : node.state === "witness"
                  ? UNTOUCHED_COLOR
                  : WITNESS_COLOR,
          born,
        };
      }),
    );
  }, [station]);

  return (
    <group position={position}>
      <TwinCore alarm={alarm} active={!dormant} />
      {station.nodes.map((node, index) => (
        <DataNode
          key={node.sensor}
          index={index}
          count={station.nodes.length}
          node={node}
          dormant={dormant}
          levelColor={station.color}
          level={station.level}
          hour={station.hour}
        />
      ))}
      <Pulses pulses={pulses} now={() => clockRef.current} />
      {!dormant && station.rain && station.rain.mm > 0.05 && <RainField rain={station.rain} />}
    </group>
  );
}

function Scene({
  stations,
  activeStationId,
  alarm,
}: {
  stations: StationInspector[];
  activeStationId: string;
  alarm: StationAlarm | null;
}) {
  return (
    <>
      <ambientLight intensity={0.6} />
      <hemisphereLight args={["#ffffff", "#444444", 0.8]} />
      <directionalLight
        position={[20, 30, 20]}
        intensity={2.0}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-50}
        shadow-camera-right={50}
        shadow-camera-top={50}
        shadow-camera-bottom={-50}
        shadow-bias={-0.0005}
      />

      <FarmEnvironment />

      {stations.map((station, index) => {
        const pos = STATION_COORDS[index % STATION_COORDS.length];
        const isActive = station.id === activeStationId;
        const stationAlarm = isActive ? alarm : null;

        return (
          <StationCluster key={station.id} station={station} alarm={stationAlarm} position={pos} />
        );
      })}

      <ContactShadows position={[0, 0.05, 0]} opacity={0.4} scale={100} blur={2.5} far={30} />

      <OrbitControls
        makeDefault
        enablePan={true}
        minDistance={20}
        maxDistance={100}
        maxPolarAngle={Math.PI / 2.1}
        target={[0, 0, 0]}
        autoRotate={false}
      />
    </>
  );
}

export default function StationScene({
  stations,
  activeStationId,
  alarm = null,
}: {
  stations: StationInspector[];
  activeStationId: string;
  alarm: StationAlarm | null;
}) {
  const { theme } = useTheme();
  const background = theme === "dark" ? "#0b0f16" : "#e2e8f0";

  return (
    <Canvas
      shadows
      dpr={[1, 2]}
      // Camera angle adjusted to perfectly center the red circle area
      camera={{ position: [25, 22, 30], fov: 40 }}
      gl={{ antialias: true }}
      style={{ background, borderRadius: "0.75rem" }}
    >
      <fog attach="fog" args={[background, 40, 120]} />
      <Scene stations={stations} activeStationId={activeStationId} alarm={alarm} />
    </Canvas>
  );
}
