import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { fetchCurrentUser } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/logo";
import {
  Activity,
  Bot,
  Droplets,
  Gauge,
  Radar,
  Satellite,
  Shield,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  Waypoints,
} from "lucide-react";

export const Route = createFileRoute("/")({
  component: Landing,
});

const ORBS: Array<{ top: string; left: string; size: number; color: string; delay: number }> = [
  { top: "-12%", left: "-6%", size: 520, color: "#e9d5ff99", delay: 0 },
  { top: "55%", left: "72%", size: 640, color: "#d8b4fe88", delay: 1.3 },
  { top: "8%", left: "58%", size: 380, color: "#f3e8ff99", delay: 2.1 },
  { top: "62%", left: "2%", size: 460, color: "#c084fc77", delay: 0.7 },
  { top: "30%", left: "34%", size: 260, color: "#ddd6fe88", delay: 1.7 },
];

// BUSINESS-FOCUSED FEATURES
const FEATURES = [
  {
    icon: ShieldCheck,
    title: "Uncompromised Data Integrity",
    desc: "Protect critical infrastructure from sophisticated False Data Injection. Our engine detects complex anomalies in real-time, ensuring your environmental data remains a reliable source of truth.",
    accent: "#7c3aed",
  },
  {
    icon: Radar,
    title: "Actionable Threat Intelligence",
    desc: "Don't just detect—understand. Every alert is instantly categorized from stealthy drifts to replay attacks, empowering your security team with targeted, rapid incident response.",
    accent: "#6366f1",
  },
  {
    icon: Bot,
    title: "Audit-Ready Explainability",
    desc: "Bridge the gap between complex AI and human operators. Transparent verdicts provide statistical and physical evidence for every alert, ensuring compliance and operational trust.",
    accent: "#8b5cf6",
  },
];

const PIPELINE = [
  { icon: UploadCloud, label: "Ingest", detail: "Data validation" },
  { icon: Gauge, label: "Analyze", detail: "Real-time scoring" },
  { icon: Radar, label: "Classify", detail: "Threat intelligence" },
  { icon: Waypoints, label: "Explain", detail: "Evidence generation" },
  { icon: Sparkles, label: "Resolve", detail: "Actionable insights" },
];

function Landing() {
  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    fetchCurrentUser().then((user) => setAuthed(user !== null));
  }, []);

  return (
    <div className="relative h-[100dvh] overflow-hidden bg-[#faf8ff] text-slate-900 flex flex-col">
      {/* ---- Light airy backdrop + floating blurred orbs ------------------- */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(1200px 700px at 15% -10%, #f3e8ffcc, transparent 60%)," +
            "radial-gradient(900px 600px at 85% 100%, #e0e7ffb3, transparent 55%)," +
            "radial-gradient(700px 500px at 60% 40%, #fae8ff80, transparent 60%)",
        }}
      />
      <style>{`
        @keyframes orbFloat {
          0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
          50% { transform: translate3d(0, -3rem, 0) scale(1.06); }
        }
        @keyframes orbSpin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          .landing-orb, .landing-orbit { animation: none !important; }
        }
      `}</style>
      {ORBS.map((orb, index) => (
        <div
          key={index}
          aria-hidden
          className="landing-orb pointer-events-none absolute rounded-full blur-[110px]"
          style={{
            top: orb.top,
            left: orb.left,
            width: orb.size,
            height: orb.size,
            background: orb.color,
            animation: `orbFloat 14s ease-in-out ${orb.delay}s infinite`,
          }}
        />
      ))}
      <div
        aria-hidden
        className="landing-orbit pointer-events-none absolute -right-40 top-1/3 h-[42rem] w-[42rem] rounded-full border border-purple-200/50"
        style={{
          transform: "rotate(-18deg) scaleY(0.42)",
          animation: "orbSpin 90s linear infinite",
          transformOrigin: "center",
        }}
      />

      {/* ---- Header ---------------------------------------------------- */}
      <header className="relative z-10 mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2">
          <Logo className="h-8 w-8" />
          <span className="text-lg font-bold tracking-tight text-slate-900">MIRSAAD</span>
          <span className="hidden text-sm font-medium text-slate-500 md:inline">
            · Ground Truth Under Attack
          </span>
        </div>
        <Button asChild variant={authed ? "default" : "outline"} className={authed ? "" : "border-slate-300 bg-white/50 backdrop-blur-md text-slate-700 hover:bg-white"}>
          <Link to={authed ? "/dashboard" : "/auth"}>{authed ? "Open console" : "Sign in"}</Link>
        </Button>
      </header>

      {/* ---- Main Content (Flex-1 to fill remaining space) ---- */}
      <main className="relative z-10 mx-auto flex w-full max-w-6xl flex-1 flex-col justify-center px-6 pb-6">
        <div className="max-w-4xl">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-purple-200/60 bg-white/40 px-3 py-1 text-xs font-bold text-purple-700 backdrop-blur-md shadow-sm">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-purple-500" />
            IEEE MIRSAAD · Track 2 · Ground Truth Under Attack
          </div>
          
          <h1 className="text-3xl font-extrabold leading-tight tracking-tight text-slate-900 md:text-5xl lg:text-[3.25rem]">
            Securing environmental IoT networks against{" "}
            <span className="bg-clip-text text-transparent [background-image:linear-gradient(90deg,#7e22ce,#6366f1,#0284c7)]">
              False Data Injection
            </span>
            .
          </h1>
          
         
          <div className="mt-6 flex flex-wrap gap-3">
            <Button asChild size="default" className="bg-purple-600 text-white hover:bg-purple-700 font-semibold px-6 shadow-md shadow-purple-500/20">
              <Link to={authed ? "/dashboard" : "/auth"}>{authed ? "Enter console" : "Get started"}</Link>
            </Button>
            
          </div>
        </div>

        {/* ---- Pipeline ribbon ------------------------------------------ */}
        <div className="mt-8 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {PIPELINE.map(({ icon: Icon, label, detail }, index) => (
            <div
              key={label}
              className="flex items-center gap-3 rounded-xl border border-white/60 bg-white/30 px-3 py-2.5 shadow-sm backdrop-blur-lg"
            >
              <Icon className="h-4 w-4 shrink-0 text-purple-500" />
              <div className="leading-tight">
                <p className="text-xs font-bold text-slate-800">
                  {index + 1} · {label}
                </p>
                <p className="text-[10px] font-semibold text-slate-500">{detail}</p>
              </div>
            </div>
          ))}
        </div>

        {/* ---- 3 Feature cards (Glassmorphism transparent & blurry) ---- */}
        <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, desc, accent }) => (
            <div
              key={title}
              className="group relative flex flex-col overflow-hidden rounded-2xl border border-white/50 bg-white/40 p-6 shadow-sm backdrop-blur-2xl transition-all duration-300 hover:-translate-y-1 hover:border-white/80 hover:bg-white/60 hover:shadow-lg"
            >
              {/* Inner glow effect for the glass */}
              <div className="pointer-events-none absolute inset-0 rounded-2xl border border-white/30 mix-blend-overlay"></div>
              
              <div
                aria-hidden
                className="pointer-events-none absolute -right-14 -top-14 h-32 w-32 rounded-full opacity-0 blur-3xl transition-opacity duration-300 group-hover:opacity-100"
                style={{ background: `${accent}30` }}
              />
              <div className="flex items-center gap-3 relative z-10">
                <span
                  className="grid size-10 shrink-0 place-items-center rounded-xl border border-white/60 shadow-sm"
                  style={{ background: `linear-gradient(135deg, ${accent}20, ${accent}05)` }}
                >
                  <Icon className="h-5 w-5" style={{ color: accent }} />
                </span>
                <h3 className="text-lg font-bold tracking-tight text-slate-900">{title}</h3>
              </div>
              <p className="mt-3 text-sm font-medium leading-relaxed text-slate-700 relative z-10">{desc}</p>
            </div>
          ))}
        </div>

        {/* ---- Telemetry ribbon ---------------------------------------- */}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-8 gap-y-3 rounded-xl border border-white/60 bg-white/30 px-6 py-3 text-[11px] font-bold text-slate-600 shadow-sm backdrop-blur-lg">
          <span className="inline-flex items-center gap-2">
            <Satellite className="h-4 w-4 text-purple-500" /> 5 reference stations · 173k station-hours
          </span>
          <span className="inline-flex items-center gap-2">
            <Droplets className="h-4 w-4 text-blue-500" /> 5 cm probe vs. untouchable witnesses
          </span>
          <span className="inline-flex items-center gap-2">
            <Activity className="h-4 w-4 text-amber-500" /> live threshold p ≥ 0.431
          </span>
          <span className="inline-flex items-center gap-2">
            <Shield className="h-4 w-4 text-emerald-500" /> verdict locked — AI explains, never overrides
          </span>
        </div>
      </main>
    </div>
  );
}