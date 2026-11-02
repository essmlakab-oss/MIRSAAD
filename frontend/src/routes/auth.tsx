import { createFileRoute, useNavigate, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Loader2, ChevronLeft } from "lucide-react";

import { fetchCurrentUser, login, register } from "@/lib/auth";
import { Logo } from "@/components/logo";

export const Route = createFileRoute("/auth")({
  head: () => ({
    meta: [
      { title: "Sign in — MIRSAAD" },
      { name: "description", content: "Access the MIRSAAD Ground Truth Under Attack console." },
    ],
  }),
  component: AuthPage,
});

// Same Orbs from Landing Page
const ORBS: Array<{ top: string; left: string; size: number; color: string; delay: number }> = [
  { top: "-12%", left: "-6%", size: 520, color: "#e9d5ff99", delay: 0 },
  { top: "55%", left: "72%", size: 640, color: "#d8b4fe88", delay: 1.3 },
  { top: "8%", left: "58%", size: 380, color: "#f3e8ff99", delay: 2.1 },
  { top: "62%", left: "2%", size: 460, color: "#c084fc77", delay: 0.7 },
  { top: "30%", left: "34%", size: 260, color: "#ddd6fe88", delay: 1.7 },
];

function AuthPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [organization, setOrganization] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchCurrentUser().then((user) => {
      if (user) navigate({ to: "/dashboard" });
    });
  }, [navigate]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === "signup") {
        const session = await register({
          email,
          password,
          full_name: fullName.trim() || undefined,
          organization: organization.trim() || undefined,
        });

        if (session.email_confirmation_required) {
          toast.success(`Account created. Check ${session.user.email} for the confirmation link.`);
          setMode("signin");
          setPassword("");
          return;
        }

        toast.success(`Account created. Welcome to MIRSAAD, ${session.user.email}.`);
      } else {
        await login({ email, password });
        toast.success("Signed in.");
      }
      navigate({ to: "/dashboard" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Authentication failed";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex h-[100dvh] flex-col overflow-hidden bg-[#faf8ff] text-slate-900 selection:bg-purple-200">
      
      {/* ---- Dynamic Background (Matching Landing Page) ---- */}
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

      {/* ---- Back to Home Navigation ---- */}
      <div className="relative z-20 mx-auto flex w-full max-w-6xl items-center px-6 py-6">
        <Link to="/" className="inline-flex items-center gap-2 text-sm font-semibold text-slate-500 transition-colors hover:text-purple-600">
          <ChevronLeft className="h-4 w-4" />
          Back to home
        </Link>
      </div>

      {/* ---- Auth Card Container ---- */}
      <div className="relative z-10 flex flex-1 items-center justify-center px-4 pb-12">
        <div 
          className="w-full max-w-md rounded-2xl border border-white/50 bg-white/40 p-8 shadow-sm backdrop-blur-2xl sm:p-10"
        >
          
          {/* Header / Logo */}
          <div className="mb-8 flex flex-col items-center text-center">
            <div className="mb-4 grid size-12 place-items-center rounded-xl bg-white/60 shadow-sm ring-1 ring-white/80 backdrop-blur-md">
              <Logo className="h-6 w-6" />
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 sm:text-3xl">
              {mode === "signin" ? "Welcome back" : "Create your account"}
            </h1>
            <p className="mt-2 text-sm font-medium text-slate-500">
              {mode === "signin"
                ? "Sign in."
                : "Create an account in seconds to access the console."}
            </p>
          </div>

          <form onSubmit={submit} className="space-y-4">
            {mode === "signup" && (
              <>
                <div className="space-y-1.5">
                  <Label htmlFor="full-name" className="text-xs font-bold text-slate-700">Full name</Label>
                  <Input
                    id="full-name"
                    autoComplete="name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="Amin Bali"
                    className="border-white/60 bg-white/50 text-slate-900 placeholder:text-slate-400 focus-visible:ring-purple-500/50"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="organization" className="text-xs font-bold text-slate-700">Organization</Label>
                  <Input
                    id="organization"
                    autoComplete="organization"
                    value={organization}
                    onChange={(e) => setOrganization(e.target.value)}
                    placeholder="IEEE AESS"
                    className="border-white/60 bg-white/50 text-slate-900 placeholder:text-slate-400 focus-visible:ring-purple-500/50"
                  />
                </div>
              </>
            )}
            
            <div className="space-y-1.5">
              <Label htmlFor="email" className="text-xs font-bold text-slate-700">Email</Label>
              <Input
                id="email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="analyst@MIRSAAD.dev"
                className="border-white/60 bg-white/50 text-slate-900 placeholder:text-slate-400 focus-visible:ring-purple-500/50"
              />
            </div>
            
            <div className="space-y-1.5">
              <Label htmlFor="password" className="text-xs font-bold text-slate-700">Password</Label>
              <Input
                id="password"
                type="password"
                required
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="border-white/60 bg-white/50 text-slate-900 placeholder:text-slate-400 focus-visible:ring-purple-500/50"
              />
              {mode === "signup" && (
                <p className="text-[11px] font-medium text-slate-500">
                  At least 8 characters, with one letter and one digit.
                </p>
              )}
            </div>

            <Button 
              type="submit" 
              className="mt-2 w-full bg-purple-600 font-semibold text-white shadow-md shadow-purple-500/20 transition-all hover:bg-purple-700 hover:shadow-lg" 
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : mode === "signin" ? (
                "Sign in"
              ) : (
                "Create account"
              )}
            </Button>
          </form>

          <div className="mt-6 text-center text-sm font-medium text-slate-500">
            {mode === "signin" ? (
              <>
                New to MIRSAAD?{" "}
                <button
                  type="button"
                  onClick={() => setMode("signup")}
                  className="font-bold text-purple-600 transition-colors hover:text-purple-700 hover:underline"
                >
                  Create an account
                </button>
              </>
            ) : (
              <>
                Already registered?{" "}
                <button
                  type="button"
                  onClick={() => setMode("signin")}
                  className="font-bold text-purple-600 transition-colors hover:text-purple-700 hover:underline"
                >
                  Sign in
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}