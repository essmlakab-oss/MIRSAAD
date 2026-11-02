import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import { Pause, Play, RotateCcw, Shuffle, SkipForward, Swords } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChartContainer, type ChartConfig } from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { Station3D, type StationAlarm, type StationInspector } from "@/components/station-3d";
import {
  arenaApi,
  ARENA_COLORS,
  FAMILY_LABEL,
  STATION_LABEL,
  fmtAmp,
  fmtDuration,
  type ArenaFamilyKey,
  type ArenaFrames,
  type ArenaMeta,
  type ArenaScenario,
} from "@/lib/arena-api";
import { cn } from "@/lib/utils";

/**
 * Duel — one attack, played as a five-step film:
 *   1 Scenario (attacker picks) · 2 Injection (values are rewritten) ·
 *   3 Witnesses (defender cross-checks the sensors the attacker cannot reach) ·
 *   4 Score (two-stage model, probability vs threshold) · 5 Decision.
 * Every number on screen is the engine's own payload for that attack.
 */

const SCENES = [
  { key: "scenario", title: "Scenario", sub: "attacker chooses", d: 2.6, who: "attacker" },
  { key: "injection", title: "Injection", sub: "values are faked", d: 3.6, who: "attacker" },
  { key: "witnesses", title: "Witnesses", sub: "defender cross-checks", d: 4.6, who: "defender" },
  { key: "score", title: "Score", sub: "two-stage model", d: 4.0, who: "defender" },
  { key: "decision", title: "Decision", sub: "alert or not", d: 2.4, who: "defender" },
] as const;
const TOTAL = SCENES.reduce((a, s) => a + s.d, 0);

function sceneAt(t: number) {
  let acc = 0;
  for (let i = 0; i < SCENES.length; i++) {
    if (t < acc + SCENES[i].d || i === SCENES.length - 1)
      return { i, p: Math.min(1, (t - acc) / SCENES[i].d) };
    acc += SCENES[i].d;
  }
  return { i: SCENES.length - 1, p: 1 };
}

const SENSOR_NAME: Record<string, string> = {
  SOIL_MOISTURE_5: "5 cm",
  SOIL_MOISTURE_10: "10 cm",
  P_CALC: "rain",
};

/** Short names for the witness heatmap rows (full sentence in the tooltip). */
const WITNESS_SHORT: Record<string, string> = {
  tu5_cdev: "5 cm vs untouched sensors",
  box_tu5_absmax: "5 cm level shift",
  pair5_24: "rise-then-fall pair",
  inc5_24: "24 h change unexplained",
  rise_nosupport72: "wetter without rain",
  rain_missing24: "rain missing",
  rain_implaus24: "rain without weather",
  ratio1020_cdev: "10 / 20 cm ratio",
  dup_later_m24: "copied sequence",
  raindup_m24: "copied storm",
  nb_rain_vs72: "rain vs neighbours",
  tE5_cdev: "5 cm vs ERA5",
};

/** What the film points at, computed once per attack from the engine payload. */
type Events = {
  i0: number;
  i1: number;
  peak: number;
  /** half-width of the normal zone: 95 % of hours keep the true value this close to the twin */
  band: number;
  firstWit: { k: number; key: string; text: string } | null;
  firstAlert: number | null;
  segments: [number, number][];
  /** y-range of the lie strip, never narrower than the noise band */
  lieLo: number;
  lieHi: number;
};

const WIT_LIT = 0.98;
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const hLabel = (k: number, i0: number) => (k - i0 > 0 ? `h+${k - i0}` : "at start");

function caption(F: ArenaFrames, i: number, ev: Events): string {
  const fam = FAMILY_LABEL[F.manifest.family]?.toLowerCase() ?? F.manifest.family;
  const st = STATION_LABEL[F.station] ?? F.station;
  const changed = (F.manifest.changed ?? ["SOIL_MOISTURE_5"])
    .map((c) => SENSOR_NAME[c] ?? c)
    .join(", ");
  const top = F.decision.type_top3[0]?.label?.toLowerCase();
  switch (i) {
    case 0:
      return `The attacker picks a ${fam} on ${st}, for ${fmtDuration(F.manifest.duration_h)} from ${F.manifest.start.slice(0, 16)}.`;
    case 1:
      return `${changed} now report something else — the 5 cm probe is moved by up to ${F.decision.damage_max.toFixed(3)} m³/m³, while its natural noise here is about ±${ev.band.toFixed(3)}. The untouched sensors keep telling the truth.`;
    case 2:
      return ev.firstWit
        ? `${cap(hLabel(ev.firstWit.k, ev.i0))}: the first cross-check lights up — ${ev.firstWit.text}.`
        : "The defender rebuilds the soil from the untouched sensors and runs ~200 cross-checks: none becomes rarer than 98 % of normal hours.";
    case 3:
      return ev.firstAlert !== null
        ? `${cap(hLabel(ev.firstAlert, ev.i0))}: the probability crosses the ${Math.round(F.threshold * 100)} % threshold — alert.` +
            (!ev.firstWit || ev.firstWit.k > ev.firstAlert
              ? " Before any single cross-check became extreme: the model adds up many mild signals."
              : "")
        : `The probability stays under the ${Math.round(F.threshold * 100)} % threshold (peak ${Math.round(F.decision.max_proba * 100)} %).`;
    default:
      return F.decision.detected
        ? `Alert — ${F.decision.verdict.toLowerCase()}, ${Math.round((F.decision.hours_detected ?? 0) * 100)} % of the attacked hours flagged; most likely a ${top}.`
        : "No alert: this attack stays inside natural variability. It is logged as a weakness.";
  }
}

type Row = {
  t: number;
  truth: number | null;
  reported: number | null;
  expected: number | null;
  band: [number, number] | null;
  lieArea: [number, number] | null;
  lie: number | null;
  truth10: number | null;
  reported10: number | null;
  rainTrue: number | null;
  rainReported: number | null;
  proba: number | null;
};

const chartConfig = {
  truth: { label: "True value", color: ARENA_COLORS.detected },
  band: { label: "Normal zone", color: "#a78bfa" },
  lie: { label: "The lie", color: ARENA_COLORS.attack },
  reported: { label: "Reported (faked)", color: "#0ea5e9" },
  expected: { label: "Expected (twin)", color: "#a78bfa" },
  proba: { label: "Attack probability", color: ARENA_COLORS.missed },
} satisfies ChartConfig;

const card = "rounded-2xl border border-border/60 bg-card p-4";
const shadow = { boxShadow: "var(--shadow-elegant)" };

export function ArenaDuel({
  meta,
  frames,
  onFrames,
  ready,
}: {
  meta: ArenaMeta | null;
  frames: ArenaFrames | null;
  onFrames: (f: ArenaFrames | null, autoplay: boolean) => void;
  ready: boolean;
}) {
  // ---------------- scenario form
  const [family, setFamily] = useState<ArenaFamilyKey>("hidden_storm");
  const [station, setStation] = useState("NY_Ithaca_13_E");
  const [start, setStart] = useState("2025-04-15");
  const [amp, setAmp] = useState<number>(0.03);
  const [dur, setDur] = useState<number>(72);
  const [sensors, setSensors] = useState("5+10+rain");
  const [shape, setShape] = useState("smooth");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fam = meta?.families.find((f) => f.key === family) ?? null;
  const ampEnabled = !!fam && fam.default.amplitude !== null && family !== "fake_storm";
  const shapeEnabled =
    !!fam && !["replay", "fake_storm", "hidden_storm", "stealth"].includes(family);

  const pick = useCallback(
    (k: ArenaFamilyKey) => {
      setFamily(k);
      const f = meta?.families.find((x) => x.key === k);
      if (f) {
        setDur(f.default.duration_h);
        if (f.default.amplitude !== null) setAmp(f.default.amplitude);
        setSensors(f.default.sensors);
        if (f.default.shape) setShape(f.default.shape);
      }
    },
    [meta],
  );

  const scenario = (): ArenaScenario => {
    const sc: ArenaScenario = {
      family,
      station,
      start: `${start} 06:00`,
      duration_h: dur,
      sensors,
    };
    if (ampEnabled) sc.amplitude = amp;
    if (family === "fake_storm") sc.amplitude = 1.0;
    if (shapeEnabled) sc.shape = shape;
    return sc;
  };

  async function launch(sc?: ArenaScenario) {
    setBusy(true);
    setError(null);
    try {
      const F = await arenaApi.simulate(sc ?? scenario());
      if (F.status !== "applied") setError(`Attack refused: ${F.reason}`);
      else onFrames(F, true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function random() {
    if (!meta) return;
    const ks = meta.families.map((f) => f.key);
    const k = ks[Math.floor(Math.random() * ks.length)];
    const st = meta.stations[Math.floor(Math.random() * meta.stations.length)];
    const d = new Date(
      Date.UTC(2025, 1 + Math.floor(Math.random() * 9), 1 + Math.floor(Math.random() * 27)),
    );
    pick(k);
    setStation(st);
    setStart(d.toISOString().slice(0, 10));
  }

  // keep the form in sync with a replayed / loaded attack
  useEffect(() => {
    if (!frames) return;
    const m = frames.manifest;
    setFamily(m.family);
    setStation(m.station);
    setStart(String(m.start).slice(0, 10));
    setDur(m.duration_h);
    const a = (frames.scenario?.amplitude ?? null) as number | null;
    if (a !== null && m.family !== "fake_storm") setAmp(a);
    if (frames.scenario?.sensors) setSensors(frames.scenario.sensors);
    if (frames.scenario?.shape) setShape(frames.scenario.shape);
  }, [frames]);

  // ---------------- player
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const raf = useRef<number | null>(null);
  const last = useRef(0);

  useEffect(() => {
    setT(0);
    setPlaying(!!frames);
  }, [frames]);

  useEffect(() => {
    if (!playing) return;
    last.current = performance.now();
    const tick = (now: number) => {
      const dt = ((now - last.current) / 1000) * speed;
      last.current = now;
      setT((prev) => {
        const next = Math.min(TOTAL, prev + dt);
        if (next >= TOTAL) setPlaying(false);
        return next;
      });
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [playing, speed]);

  const { i: step, p } = sceneAt(t);
  // 10 fps is enough for the charts; the 3D scene animates on its own clock
  const tq = Math.round(t * 10) / 10;

  // ---------------- derived frames
  const idx = useMemo(() => {
    if (!frames) return null;
    const T = frames.time;
    const i0 = Math.max(
      0,
      T.findIndex((x) => x >= frames.attack_window[0].slice(0, 16)),
    );
    const i1 = Math.max(
      i0,
      T.findIndex((x) => x >= frames.attack_window[1].slice(0, 16)),
    );
    let peak = i0;
    let best = -1;
    for (let k = i0; k <= i1; k++) {
      const a = frames.series.att5[k];
      const c = frames.series.clean5[k];
      const d = a !== null && c !== null ? Math.abs(a - c) : 0;
      const r =
        Math.abs((frames.series.att_rain[k] ?? 0) - (frames.series.clean_rain[k] ?? 0)) / 20;
      if (d + r > best) {
        best = d + r;
        peak = k;
      }
    }
    return { i0, i1: i1 < 0 ? T.length - 1 : i1, peak };
  }, [frames]);

  const ev = useMemo<Events | null>(() => {
    if (!frames || !idx) return null;
    const S = frames.series;
    const n = frames.time.length;
    // normal zone: how far the true value usually wanders from the twin around this period
    const dev: number[] = [];
    for (let k = 0; k < n; k++) {
      const c = S.clean5[k];
      const e = S.expected5[k];
      if (c !== null && e !== null) dev.push(Math.abs(c - e));
    }
    dev.sort((a, b) => a - b);
    const band = Math.max(0.003, dev.length ? dev[Math.floor(0.95 * (dev.length - 1))] : 0.01);
    const lo = Math.max(0, idx.i0 - 6);
    const hi = Math.min(n - 1, idx.i1 + 24);
    let firstWit: Events["firstWit"] = null;
    for (let k = idx.i0; k <= hi && !firstWit; k++)
      for (const w of frames.witnesses)
        if ((w.values[k] ?? 0) >= WIT_LIT) {
          firstWit = { k, key: w.key, text: w.text };
          break;
        }
    let firstAlert: number | null = null;
    for (let k = lo; k <= hi; k++)
      if (frames.scores.alert[k]) {
        firstAlert = k;
        break;
      }
    const segments: [number, number][] = [];
    for (let k = 0; k < n; k++) {
      if (!frames.scores.alert[k]) continue;
      const last = segments[segments.length - 1];
      if (last && last[1] === k - 1) last[1] = k;
      else segments.push([k, k]);
    }
    let lieLo = -band * 1.5;
    let lieHi = band * 1.5;
    for (let k = 0; k < n; k++) {
      const a = S.att5[k];
      const c = S.clean5[k];
      if (a === null || c === null) continue;
      lieLo = Math.min(lieLo, a - c);
      lieHi = Math.max(lieHi, a - c);
    }
    return { ...idx, band, firstWit, firstAlert, segments, lieLo, lieHi };
  }, [frames, idx]);

  const nHours = frames?.time.length ?? 0;
  // how far each defender layer has been revealed (hour index)
  const witSweep = step < 2 ? -1 : step === 2 ? Math.floor(p * (nHours - 1)) : nHours - 1;
  const scoreSweep = step < 3 ? -1 : step === 3 ? Math.floor(p * (nHours - 1)) : nHours - 1;
  const showWit = !!ev?.firstWit && witSweep >= ev.firstWit.k;
  const showAlert =
    ev?.firstAlert !== null && ev?.firstAlert !== undefined && scoreSweep >= ev.firstAlert;

  // witness and alert markers closer than ~4 % of the chart share one label
  const witNearAlert =
    !!ev?.firstWit &&
    ev.firstAlert !== null &&
    Math.abs(ev.firstWit.k - ev.firstAlert) < Math.max(3, nHours * 0.04);

  const rows = useMemo<Row[]>(() => {
    if (!frames || !idx || !ev) return [];
    const S = frames.series;
    const morph = step === 0 ? 0 : step === 1 ? Math.min(1, p * 1.2) : 1;
    const mix = (c: number | null, a: number | null) =>
      step === 0 ? null : c !== null && a !== null ? c + morph * (a - c) : a;
    return frames.time.map((_tt, k) => {
      const c = S.clean5[k];
      const rep = mix(c, S.att5[k]);
      const e = S.expected5[k];
      const lie = rep !== null && c !== null ? rep - c : null;
      return {
        t: k,
        truth: c,
        reported: rep,
        expected: step >= 2 ? e : null,
        band: step >= 2 && e !== null ? [e - ev.band, e + ev.band] : null,
        lieArea:
          lie !== null && c !== null && rep !== null && Math.abs(lie) > 2e-4
            ? [Math.min(c, rep), Math.max(c, rep)]
            : null,
        lie,
        truth10: S.clean10[k],
        reported10: mix(S.clean10[k], S.att10[k]),
        rainTrue: S.clean_rain[k],
        rainReported: mix(S.clean_rain[k], S.att_rain[k]),
        proba: k <= scoreSweep ? (frames.scores.proba[k] ?? null) : null,
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frames, idx, ev, step, tq]);

  const changedSet = useMemo(() => new Set(frames?.manifest.changed ?? []), [frames]);

  const shownHour = idx ? (step === 0 ? Math.max(0, idx.i0 - 12) : idx.peak) : 0;

  // ---------------- 3D inspector (their Data Inspector component)
  const inspector = useMemo<StationInspector>(() => {
    const id = frames?.station ?? station;
    if (!frames) return { id, hour: null, level: null, color: null, nodes: [] };
    const k = shownHour;
    const faked = step >= 1;
    const decided = step === 4;
    const level = decided
      ? frames.decision.detected
        ? "ATTAQUE"
        : "normal"
      : faked
        ? "SUSPECT"
        : "normal";
    const color = decided
      ? frames.decision.detected
        ? ARENA_COLORS.missed
        : ARENA_COLORS.detected
      : faked
        ? ARENA_COLORS.attack
        : ARENA_COLORS.detected;
    const S = frames.series;
    const pick5 = faked ? S.att5[k] : S.clean5[k];
    const pick10 = faked ? S.att10[k] : S.clean10[k];
    const pickR = faked ? S.att_rain[k] : S.clean_rain[k];
    const f10 = faked && changedSet.has("SOIL_MOISTURE_10");
    const fR = faked && changedSet.has("P_CALC");
    const trueR = S.clean_rain[k] ?? 0;
    const repR = pickR ?? 0;
    const rain =
      fR && repR > trueR + 0.05
        ? { mm: repR, kind: "fake" as const }
        : fR && trueR > repR + 0.05
          ? { mm: trueR, kind: "hidden" as const }
          : repR > 0.05
            ? { mm: repR, kind: "real" as const }
            : null;
    return {
      id,
      hour: frames.time[k]?.slice(5, 16) ?? null,
      level,
      color,
      rain,
      nodes: [
        {
          sensor: "sm5",
          role: faked ? "Target · faked" : "Target",
          value: pick5 ?? null,
          unit: "m³/m³",
          decimals: 3,
          state: faked ? "faked" : undefined,
        },
        {
          sensor: "sm10",
          role: f10 ? "10 cm · faked" : faked ? "10 cm · witness" : "10 cm",
          value: pick10 ?? null,
          unit: "m³/m³",
          decimals: 3,
          state: f10 ? "faked" : faked ? "witness" : undefined,
        },
        {
          sensor: "rain",
          role: fR ? "Rain · faked" : faked ? "Rain · witness" : "Weather",
          value: pickR ?? null,
          unit: "mm",
          decimals: 1,
          state: fR ? "faked" : faked ? "witness" : undefined,
        },
        {
          sensor: "sm20",
          role: faked ? "20 cm · untouched" : "20 cm",
          value: S.sm20[k] ?? null,
          unit: "m³/m³",
          decimals: 3,
          state: faked ? "witness" : undefined,
        },
      ],
    };
  }, [frames, station, shownHour, step, changedSet]);

  const alarm = useMemo<StationAlarm | null>(() => {
    if (!frames || step < 4 || !frames.decision.detected) return null;
    return {
      color: ARENA_COLORS.missed,
      label: "Attack detected",
      detail: `${frames.decision.type_top3[0]?.label ?? ""} · peak ${(frames.decision.max_proba * 100).toFixed(0)} %`,
    };
  }, [frames, step]);

  const thr = frames?.threshold ?? meta?.threshold ?? 0.445;
  const probShown =
    frames && idx
      ? step < 3
        ? null
        : (frames.scores.proba[idx.peak] ?? 0) * (step === 3 ? p : 1)
      : null;

  return (
    <div className="grid gap-4 lg:grid-cols-[17rem_minmax(0,1fr)_17rem]">
      {/* ---------------- attacker: scenario ---------------- */}
      <section className={card} style={shadow} data-tour="arena-scenario">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-tight">Attack scenario</h2>
          <span
            className="text-[10px] font-semibold uppercase tracking-wider"
            style={{ color: ARENA_COLORS.attack }}
          >
            Attacker
          </span>
        </div>

        <FieldLabel>Attack type</FieldLabel>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {(meta?.families ?? []).map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => pick(f.key)}
              aria-pressed={family === f.key}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs transition-colors",
                family === f.key
                  ? "border-transparent text-white"
                  : "border-border bg-background hover:bg-accent",
              )}
              style={family === f.key ? { background: ARENA_COLORS.attack } : undefined}
            >
              {f.label}
            </button>
          ))}
        </div>

        <FieldLabel>Station</FieldLabel>
        <select className={selectCls} value={station} onChange={(e) => setStation(e.target.value)}>
          {(meta?.stations ?? Object.keys(STATION_LABEL)).map((s) => (
            <option key={s} value={s}>
              {STATION_LABEL[s] ?? s}
            </option>
          ))}
        </select>

        <FieldLabel>Start (2025 — unseen by the model)</FieldLabel>
        <input
          type="date"
          min="2025-01-15"
          max="2025-12-10"
          className={selectCls}
          value={start}
          onChange={(e) => setStart(e.target.value)}
        />

        <FieldLabel
          right={ampEnabled ? fmtAmp(amp) : family === "fake_storm" ? "real storm" : "real data"}
        >
          Size
        </FieldLabel>
        <Range
          className="mb-3"
          color={ARENA_COLORS.attack}
          label="Size"
          disabled={!ampEnabled}
          min={-(fam?.limits.max_amplitude ?? 0.15)}
          max={fam?.limits.max_amplitude ?? 0.15}
          step={0.001}
          value={amp}
          onChange={setAmp}
        />

        <FieldLabel right={fmtDuration(dur)}>Duration</FieldLabel>
        <Range
          className="mb-3"
          color={ARENA_COLORS.attack}
          label="Duration"
          min={fam?.limits.min_h ?? 1}
          max={fam?.limits.max_h ?? 240}
          step={1}
          value={dur}
          onChange={setDur}
        />

        <div className="grid grid-cols-1 gap-0">
          <div>
            <FieldLabel>Shape</FieldLabel>
            <select
              className={selectCls}
              disabled={!shapeEnabled}
              value={shape}
              onChange={(e) => setShape(e.target.value)}
            >
              {(meta?.shapes ?? []).map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </select>
          </div>
          <div>
            <FieldLabel>Sensors faked</FieldLabel>
            <select
              className={selectCls}
              value={sensors}
              onChange={(e) => setSensors(e.target.value)}
            >
              {(meta?.sensors ?? ["5", "5+10", "5+10+rain"]).map((s) => (
                <option key={s} value={s}>
                  {s.replaceAll("+", " + ")}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-2 flex gap-2">
          <Button
            className="flex-1"
            onClick={() => void launch()}
            disabled={!ready || busy}
            style={{ background: ARENA_COLORS.attack }}
          >
            <Swords /> {busy ? "Computing… (~30 s)" : "Launch attack"}
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={random}
            disabled={!ready || busy}
            aria-label="Random scenario"
          >
            <Shuffle />
          </Button>
        </div>
        {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
        <p className="mt-3 text-[11px] leading-snug text-muted-foreground">
          The attack is injected into a clean copy of the station's 2025 data; the defender is the
          production detector with its digital twins.
        </p>
      </section>

      {/* ---------------- film ---------------- */}
      <section className="min-w-0 space-y-4">
        <div className={card} style={shadow}>
          <ol className="grid grid-cols-5 gap-2">
            {SCENES.map((s, k) => (
              <li key={s.key} className="min-w-0">
                <div className="h-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full transition-[width]"
                    style={{
                      width: !frames ? "0%" : k < step ? "100%" : k === step ? `${p * 100}%` : "0%",
                      background: s.who === "attacker" ? ARENA_COLORS.attack : "var(--primary)",
                    }}
                  />
                </div>
                <div
                  className={cn(
                    "mt-1.5 truncate text-xs font-medium",
                    frames && k === step ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {k + 1} · {s.title}
                </div>
                <div className="truncate text-[10px] text-muted-foreground">{s.sub}</div>
              </li>
            ))}
          </ol>

          <div className="mt-3 flex min-h-[2.75rem] items-start gap-2 rounded-xl bg-muted/60 px-3 py-2 text-sm">
            {frames ? (
              <>
                <span
                  className="mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white"
                  style={{
                    background:
                      SCENES[step].who === "attacker" ? ARENA_COLORS.attack : "var(--primary)",
                  }}
                >
                  {SCENES[step].who}
                </span>
                <span>{ev ? caption(frames, step, ev) : ""}</span>
              </>
            ) : (
              <span className="text-muted-foreground">
                Choose an attack and press <b>Launch attack</b> — or replay one from the Launcher.
              </span>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={!frames}
              onClick={() => (t >= TOTAL ? (setT(0), setPlaying(true)) : setPlaying(!playing))}
            >
              {playing ? <Pause /> : <Play />} {playing ? "Pause" : "Play"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={!frames}
              onClick={() => {
                setT(0);
                setPlaying(true);
              }}
            >
              <RotateCcw /> Restart
            </Button>
            <Range
              label="Timeline"
              className="min-w-[8rem] flex-1"
              color="var(--primary)"
              min={0}
              max={TOTAL}
              step={0.05}
              value={t}
              disabled={!frames}
              onChange={(v) => {
                setPlaying(false);
                setT(v);
              }}
            />
            <select
              className={cn(selectCls, "mb-0 w-auto")}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              aria-label="Speed"
            >
              <option value={0.5}>0.5×</option>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
            </select>
            <Button
              size="sm"
              variant="outline"
              disabled={!frames}
              onClick={() => {
                setPlaying(false);
                setT(TOTAL);
              }}
            >
              <SkipForward /> Decision
            </Button>
          </div>
        </div>

        <div className="rounded-2xl border border-border/60 bg-card p-3" style={shadow}>
          <Station3D
            inspector={inspector}
            alarm={alarm}
            overlay={
              frames && (
                <div className="pointer-events-none absolute left-3 top-3 space-y-0.5 rounded-lg border border-border/60 bg-background/80 px-3 py-2 font-mono text-[11px] backdrop-blur">
                  <div>
                    <span className="text-muted-foreground">hour shown </span>
                    {frames.time[shownHour]}
                  </div>
                  <div>
                    <span className="text-muted-foreground">5 cm true </span>
                    {fmt3(frames.series.clean5[shownHour])}
                    <span className="text-muted-foreground"> reported </span>
                    <span style={{ color: step >= 1 ? ARENA_COLORS.attack : undefined }}>
                      {fmt3(
                        step >= 1 ? frames.series.att5[shownHour] : frames.series.clean5[shownHour],
                      )}
                    </span>
                  </div>
                  {probShown !== null && (
                    <div>
                      <span className="text-muted-foreground">attack probability </span>
                      <b style={{ color: probShown >= thr ? ARENA_COLORS.missed : undefined }}>
                        {(probShown * 100).toFixed(0)} %
                      </b>
                      <span className="text-muted-foreground">
                        {" "}
                        · threshold {(thr * 100).toFixed(0)} %
                      </span>
                    </div>
                  )}
                </div>
              )
            }
          />
          <p className="mt-1 px-1 text-center text-[11px] leading-snug text-muted-foreground">
            Values at the most-faked hour. Orange rings = sensors the attacker rewrites; green =
            sensors it cannot reach (the defender's witnesses). The core turns red when the defender
            raises the alert.
          </p>
        </div>

        <div className={card} style={shadow}>
          <div className="mb-1 space-y-1">
            <h3 className="text-sm font-semibold tracking-tight">
              Soil moisture at 5 cm — what the station reports
            </h3>
            <Legend />
          </div>
          {frames ? (
            <ChartContainer config={chartConfig} className="h-56 w-full">
              <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  tickFormatter={(k: number) => frames.time[k]?.slice(5, 10) ?? ""}
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  width={44}
                  domain={["auto", "auto"]}
                  tickFormatter={(v: number) => v.toFixed(2)}
                />
                {idx && (
                  <ReferenceArea
                    x1={idx.i0}
                    x2={idx.i1}
                    fill={ARENA_COLORS.missed}
                    fillOpacity={step >= 1 ? 0.06 : 0}
                  />
                )}
                <Area
                  dataKey="band"
                  stroke="none"
                  fill="#a78bfa"
                  fillOpacity={0.16}
                  isAnimationActive={false}
                  activeDot={false}
                />
                <Area
                  dataKey="lieArea"
                  stroke="none"
                  fill={ARENA_COLORS.attack}
                  fillOpacity={0.35}
                  isAnimationActive={false}
                  activeDot={false}
                />
                {showWit && ev?.firstWit && !witNearAlert && (
                  <ReferenceLine
                    x={ev.firstWit.k}
                    stroke="#f59e0b"
                    strokeDasharray="3 3"
                    label={markLabel(`witness ${hLabel(ev.firstWit.k, ev.i0)}`, "#f59e0b", 0)}
                  />
                )}
                {showAlert && ev?.firstAlert !== null && ev && (
                  <ReferenceLine
                    x={ev.firstAlert!}
                    stroke={ARENA_COLORS.missed}
                    strokeWidth={1.5}
                    label={markLabel(
                      `ALERT ${hLabel(ev.firstAlert!, ev.i0)}${witNearAlert && showWit ? " · first witness" : ""}`,
                      ARENA_COLORS.missed,
                      witNearAlert ? 0 : 1,
                    )}
                  />
                )}
                <Line
                  dataKey="truth"
                  stroke={ARENA_COLORS.detected}
                  strokeWidth={1.4}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
                <Line
                  dataKey="expected"
                  stroke="#a78bfa"
                  strokeDasharray="5 3"
                  strokeWidth={1.4}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
                <Line
                  dataKey="reported"
                  stroke="#0ea5e9"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              </ComposedChart>
            </ChartContainer>
          ) : (
            <Skeleton className="h-56 w-full" />
          )}

          <div className="mt-2 mb-0.5 flex flex-wrap items-baseline justify-between gap-x-3">
            <h3 className="text-xs font-semibold tracking-tight">The lie — reported minus true</h3>
            <span className="text-[10px] text-muted-foreground">
              grey band = natural noise of the probe (±{ev ? ev.band.toFixed(3) : "–"} m³/m³)
            </span>
          </div>
          {frames && ev ? (
            <ChartContainer config={chartConfig} className="h-20 w-full">
              <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 8, left: 0 }}>
                <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide />
                <YAxis
                  tick={{ fontSize: 9 }}
                  tickLine={false}
                  width={44}
                  domain={[ev.lieLo, ev.lieHi]}
                  ticks={
                    Math.min(-ev.lieLo, ev.lieHi) > 0.3 * (ev.lieHi - ev.lieLo)
                      ? [ev.lieLo, 0, ev.lieHi]
                      : [ev.lieLo, ev.lieHi]
                  }
                  interval={0}
                  tickFormatter={(v: number) =>
                    v === 0 ? "0" : (v > 0 ? "+" : "−") + Math.abs(v).toFixed(3)
                  }
                />
                <ReferenceArea
                  y1={-ev.band}
                  y2={ev.band}
                  fill="var(--muted-foreground)"
                  fillOpacity={0.12}
                  stroke="none"
                />
                <ReferenceLine y={0} stroke="var(--border)" />
                <Area
                  dataKey="lie"
                  type="linear"
                  stroke={ARENA_COLORS.attack}
                  fill={ARENA_COLORS.attack}
                  fillOpacity={0.3}
                  strokeWidth={1.4}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ChartContainer>
          ) : (
            <Skeleton className="h-20 w-full" />
          )}

          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <SmallChart
              title="10 cm probe"
              faked={changedSet.has("SOIL_MOISTURE_10")}
              ready={!!frames}
            >
              {frames && (
                <ChartContainer config={chartConfig} className="h-28 w-full">
                  <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                    <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide />
                    <YAxis
                      tick={{ fontSize: 9 }}
                      tickLine={false}
                      width={40}
                      domain={["auto", "auto"]}
                      tickFormatter={(v: number) => v.toFixed(2)}
                    />
                    {idx && (
                      <ReferenceArea
                        x1={idx.i0}
                        x2={idx.i1}
                        fill={ARENA_COLORS.missed}
                        fillOpacity={step >= 1 ? 0.06 : 0}
                      />
                    )}
                    <Line
                      dataKey="truth10"
                      stroke={ARENA_COLORS.detected}
                      strokeWidth={1.3}
                      dot={false}
                      isAnimationActive={false}
                      connectNulls
                    />
                    <Line
                      dataKey="reported10"
                      stroke="#0ea5e9"
                      strokeWidth={1.8}
                      dot={false}
                      isAnimationActive={false}
                      connectNulls
                    />
                  </ComposedChart>
                </ChartContainer>
              )}
            </SmallChart>
            <SmallChart title="Rain (mm/h)" faked={changedSet.has("P_CALC")} ready={!!frames}>
              {frames && (
                <ChartContainer config={chartConfig} className="h-28 w-full">
                  <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                    <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide />
                    <YAxis
                      tick={{ fontSize: 9 }}
                      tickLine={false}
                      width={40}
                      allowDecimals={false}
                    />
                    {idx && (
                      <ReferenceArea
                        x1={idx.i0}
                        x2={idx.i1}
                        fill={ARENA_COLORS.missed}
                        fillOpacity={step >= 1 ? 0.06 : 0}
                      />
                    )}
                    <Area
                      dataKey="rainTrue"
                      type="step"
                      stroke={ARENA_COLORS.detected}
                      fill={ARENA_COLORS.detected}
                      fillOpacity={0.3}
                      strokeWidth={1}
                      dot={false}
                      isAnimationActive={false}
                    />
                    <Line
                      dataKey="rainReported"
                      type="step"
                      stroke="#0ea5e9"
                      strokeWidth={1.6}
                      dot={false}
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ChartContainer>
              )}
            </SmallChart>
          </div>

          <div className="mt-3 mb-1 flex items-center justify-between">
            <h3 className="text-sm font-semibold tracking-tight">Attack probability (stage 2)</h3>
            <span className="text-[10px] text-muted-foreground">
              dashed line = alert threshold · red = alert hours
            </span>
          </div>
          {frames ? (
            <ChartContainer config={chartConfig} className="h-32 w-full">
              <ComposedChart data={rows} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
                <CartesianGrid stroke="var(--border)" strokeOpacity={0.5} vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  tickFormatter={(k: number) => frames.time[k]?.slice(5, 10) ?? ""}
                />
                <YAxis
                  domain={[0, 1]}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  width={44}
                  tickFormatter={(v: number) => `${Math.round(v * 100)}%`}
                />
                {idx && (
                  <ReferenceArea
                    x1={idx.i0}
                    x2={idx.i1}
                    fill={ARENA_COLORS.missed}
                    fillOpacity={0.06}
                  />
                )}
                {ev?.segments
                  .filter(([a]) => a <= scoreSweep)
                  .map(([a, b]) => (
                    <ReferenceArea
                      key={a}
                      x1={a - 0.5}
                      x2={Math.min(b, scoreSweep) + 0.5}
                      fill={ARENA_COLORS.missed}
                      fillOpacity={0.22}
                      stroke="none"
                    />
                  ))}
                <ReferenceLine y={thr} stroke={ARENA_COLORS.missed} strokeDasharray="4 3" />
                {showAlert && ev && (
                  <ReferenceLine
                    x={ev.firstAlert!}
                    stroke={ARENA_COLORS.missed}
                    strokeWidth={1.5}
                  />
                )}
                <Area
                  dataKey="proba"
                  stroke="var(--primary)"
                  fill="var(--primary)"
                  fillOpacity={0.15}
                  strokeWidth={1.6}
                  dot={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ChartContainer>
          ) : (
            <Skeleton className="h-32 w-full" />
          )}
        </div>

        <div className={card} style={shadow}>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-sm font-semibold tracking-tight">
              Witness heatmap — which cross-check lit up, hour by hour
            </h3>
            <HeatLegend />
          </div>
          {frames && ev ? (
            <WitnessHeatmap frames={frames} ev={ev} reveal={witSweep} showAlert={showAlert} />
          ) : (
            <Skeleton className="h-36 w-full" />
          )}
        </div>
      </section>

      {/* ---------------- defender: decision ---------------- */}
      <section className={cn(card, "space-y-4")} style={shadow}>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-tight">Decision</h2>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-primary">
            Defender
          </span>
        </div>

        {!frames || step < 4 ? (
          <div className="rounded-xl bg-muted/60 px-3 py-3 text-sm font-medium text-muted-foreground">
            {frames ? "Analysing…" : "Waiting for an attack"}
          </div>
        ) : (
          <>
            <div
              className="rounded-xl border px-3 py-3 text-sm font-semibold"
              style={{
                color: frames.decision.detected ? ARENA_COLORS.detected : ARENA_COLORS.missed,
                borderColor: frames.decision.detected ? "#10b98155" : "#ef444455",
                background: frames.decision.detected ? "#10b98114" : "#ef444414",
              }}
            >
              {frames.decision.detected ? "✓ Attack detected" : "✕ Attack got through"}
              <div className="mt-0.5 text-xs font-normal text-muted-foreground">
                {frames.decision.verdict}
              </div>
            </div>

            <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
              <Stat
                k="First alert"
                v={
                  frames.decision.first_alert_delay_h === null
                    ? "—"
                    : frames.decision.first_alert_delay_h <= 0
                      ? "at start"
                      : `+${frames.decision.first_alert_delay_h} h`
                }
              />
              <Stat
                k="Attacked hours flagged"
                v={
                  frames.decision.hours_detected === null
                    ? "—"
                    : `${Math.round(frames.decision.hours_detected * 100)} %`
                }
              />
              <Stat k="Peak probability" v={`${Math.round(frames.decision.max_proba * 100)} %`} />
              <Stat k="Largest fake change" v={frames.decision.damage_max.toFixed(3)} />
            </dl>

            <div>
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Most likely attack type
              </div>
              {frames.decision.type_top3.map((x) => (
                <div
                  key={x.type}
                  className="mb-1 grid grid-cols-[6rem_1fr_2.5rem] items-center gap-2 text-xs"
                >
                  <span className="truncate">{x.label}</span>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${x.p * 100}%` }}
                    />
                  </div>
                  <span className="text-right font-mono text-muted-foreground">
                    {Math.round(x.p * 100)}%
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {frames && step >= 2 && (
          <div>
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              {step >= 4 && !frames.decision.detected
                ? "Closest calls — none crossed the alert"
                : "Why — the rarest witnesses"}
            </div>
            <ol className="space-y-1.5 text-xs">
              {(step >= 4 && frames.decision.reasons.length
                ? frames.decision.reasons
                : frames.witnesses.slice(0, 3).map((w) => w.text)
              ).map((r, k) => (
                <li key={k} className="flex gap-2">
                  <span className="mt-0.5 h-4 w-4 shrink-0 rounded-full bg-primary/15 text-center text-[10px] font-semibold leading-4 text-primary">
                    {k + 1}
                  </span>
                  <span className="text-muted-foreground">{r}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {frames && (
          <dl className="space-y-1 border-t border-border/60 pt-3 text-xs">
            <Stat k="Type" v={FAMILY_LABEL[frames.manifest.family] ?? frames.manifest.family} row />
            <Stat k="Station" v={STATION_LABEL[frames.station] ?? frames.station} row />
            <Stat k="Start" v={String(frames.manifest.start).slice(0, 16)} row />
            <Stat k="Duration" v={fmtDuration(frames.manifest.duration_h)} row />
            <Stat k="Compute" v={`${frames.compute_s} s`} row />
          </dl>
        )}
      </section>
    </div>
  );
}

const selectCls =
  "mb-3 h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50";

function FieldLabel({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-1 flex items-baseline justify-between">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {children}
      </span>
      {right && <span className="font-mono text-[11px] text-foreground">{right}</span>}
    </div>
  );
}

function Stat({ k, v, row = false }: { k: string; v: React.ReactNode; row?: boolean }) {
  return row ? (
    <div className="flex justify-between gap-2">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="font-mono">{v}</dd>
    </div>
  ) : (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{k}</dt>
      <dd className="font-mono text-sm">{v}</dd>
    </div>
  );
}

function swatch(c: string, label: string) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="h-2.5 w-3 rounded-sm" style={{ background: c }} />
      {label}
    </span>
  );
}

/** Label pinned to the top of a vertical marker; `row` stacks two markers that sit close together. */
function markLabel(text: string, color: string, row: number) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (props: any) => {
    const vb = props.viewBox ?? {};
    const x = (vb.x ?? 0) + 4;
    const y = (vb.y ?? 0) + 11 + row * 13;
    return (
      <text x={x} y={y} fill={color} fontSize={10} fontWeight={600}>
        {text}
      </text>
    );
  };
}

function SmallChart({
  title,
  faked,
  ready,
  children,
}: {
  title: string;
  faked: boolean;
  ready: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-xl border border-border/60 p-2.5">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{title}</span>
        {ready && (
          <span
            className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
            style={
              faked
                ? { color: ARENA_COLORS.attack, background: `${ARENA_COLORS.attack}1f` }
                : { color: ARENA_COLORS.detected, background: `${ARENA_COLORS.detected}1f` }
            }
          >
            {faked ? "faked" : "untouched"}
          </span>
        )}
      </div>
      {ready ? children : <Skeleton className="h-28 w-full" />}
    </div>
  );
}

const HEAT_STEPS = [
  { min: 0.98, opacity: 1, label: "rarer than 98 % (lit)" },
  { min: 0.95, opacity: 0.6, label: "95 %" },
  { min: 0.9, opacity: 0.3, label: "90 %" },
] as const;
const HEAT_COLOR = "#f59e0b";

function HeatLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
      <span>hour rarer than … of normal hours:</span>
      {[...HEAT_STEPS].reverse().map((s) => (
        <span key={s.min} className="inline-flex items-center gap-1">
          <span
            className="h-2.5 w-3 rounded-sm"
            style={{ background: HEAT_COLOR, opacity: s.opacity }}
          />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/**
 * One row per cross-check, one cell per hour, coloured by how rare that hour is
 * compared with normal hours of the same station (2022–24). Revealed left to
 * right during the Witnesses step; the attack window is outlined.
 */
function WitnessHeatmap({
  frames,
  ev,
  reveal,
  showAlert,
}: {
  frames: ArenaFrames;
  ev: Events;
  reveal: number;
  showAlert: boolean;
}) {
  const n = frames.time.length;
  const [hover, setHover] = useState<{ row: number; k: number } | null>(null);
  const pct = (k: number) => `${(100 * k) / n}%`;
  return (
    <div className="space-y-1">
      {frames.witnesses.map((w, row) => {
        const cells: React.ReactNode[] = [];
        for (let k = 0; k <= Math.min(reveal, n - 1); k++) {
          const v = w.values[k] ?? 0;
          const s = HEAT_STEPS.find((x) => v >= x.min);
          if (s)
            cells.push(
              <rect
                key={k}
                x={k}
                y={0}
                width={1.05}
                height={1}
                fill={HEAT_COLOR}
                fillOpacity={s.opacity}
              />,
            );
        }
        return (
          <div key={w.key} className="grid grid-cols-[9.5rem_minmax(0,1fr)] items-center gap-2">
            <span className="truncate text-[11px] text-muted-foreground" title={w.text}>
              {WITNESS_SHORT[w.key] ?? w.key}
            </span>
            <div
              className="relative h-3.5 overflow-hidden rounded-sm bg-muted/50"
              onMouseMove={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                const k = Math.floor(((e.clientX - r.left) / r.width) * n);
                setHover({ row, k: Math.max(0, Math.min(n - 1, k)) });
              }}
              onMouseLeave={() => setHover(null)}
            >
              <div
                className="absolute inset-y-0 border-x border-dashed"
                style={{
                  left: pct(ev.i0),
                  width: pct(ev.i1 - ev.i0 + 1),
                  borderColor: `${ARENA_COLORS.missed}99`,
                  background: `${ARENA_COLORS.missed}0d`,
                }}
              />
              <svg
                viewBox={`0 0 ${n} 1`}
                preserveAspectRatio="none"
                className="absolute inset-0 h-full w-full"
              >
                {cells}
              </svg>
              {showAlert && ev.firstAlert !== null && (
                <div
                  className="absolute inset-y-0 w-0.5"
                  style={{ left: pct(ev.firstAlert), background: ARENA_COLORS.missed }}
                />
              )}
              {hover?.row === row && (
                <div
                  className="absolute inset-y-0 w-px bg-foreground/70"
                  style={{ left: pct(hover.k) }}
                />
              )}
            </div>
          </div>
        );
      })}
      <div className="grid grid-cols-[9.5rem_minmax(0,1fr)] gap-2 pt-0.5 text-[10px] text-muted-foreground">
        <span />
        <div className="relative h-4">
          {hover ? (
            <span className="absolute left-0 font-mono">
              {frames.time[hover.k]} · {WITNESS_SHORT[frames.witnesses[hover.row].key]}: rarer than{" "}
              {Math.round((frames.witnesses[hover.row].values[hover.k] ?? 0) * 100)} % of normal
              hours
            </span>
          ) : (
            <>
              <span className="absolute left-0">{frames.time[0]?.slice(5, 10)}</span>
              <span
                className="absolute -translate-x-1/2"
                style={{ left: pct((ev.i0 + ev.i1) / 2), color: ARENA_COLORS.missed }}
              >
                attack window
              </span>
              <span className="absolute right-0">{frames.time[n - 1]?.slice(5, 10)}</span>
            </>
          )}
        </div>
      </div>
      {reveal < 0 && (
        <p className="text-[11px] text-muted-foreground">Lights up during step 3 · Witnesses.</p>
      )}
    </div>
  );
}

function Legend() {
  const item = (c: string, label: string, dashed = false) => (
    <span className="inline-flex items-center gap-1">
      <span
        className="h-0.5 w-4 rounded"
        style={
          dashed
            ? { backgroundImage: `repeating-linear-gradient(90deg,${c} 0 5px,transparent 5px 8px)` }
            : { background: c }
        }
      />
      {label}
    </span>
  );
  return (
    <div className="flex flex-wrap gap-x-3 text-[10px] text-muted-foreground">
      {item(ARENA_COLORS.detected, "true value")}
      {item("#0ea5e9", "reported")}
      {item("#a78bfa", "expected (untouched sensors)", true)}
      {swatch("#a78bfa33", "normal zone")}
      {swatch(`${ARENA_COLORS.attack}66`, "the lie")}
    </div>
  );
}

const fmt3 = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v.toFixed(3));

/**
 * Range slider drawn by us (track, filled part, thumb) so every browser shows
 * the same thing — Opera and some Chromium themes ignore `accent-color`.
 */
function Range({
  value,
  min,
  max,
  step,
  onChange,
  color,
  label,
  disabled,
  className,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  color: string;
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  const pct = max > min ? Math.min(100, Math.max(0, (100 * (value - min)) / (max - min))) : 0;
  return (
    <input
      type="range"
      aria-label={label}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      className={cn(
        "block h-1.5 w-full cursor-pointer appearance-none rounded-full outline-none",
        "focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-40",
        "[&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-background [&::-webkit-slider-thumb]:bg-[var(--range-c)] [&::-webkit-slider-thumb]:shadow-md",
        "[&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:w-3.5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-background [&::-moz-range-thumb]:bg-[var(--range-c)] [&::-moz-range-track]:bg-transparent",
        className,
      )}
      style={
        {
          "--range-c": color,
          background: `linear-gradient(to right, ${color} ${pct}%, var(--muted) ${pct}%)`,
        } as React.CSSProperties
      }
    />
  );
}
