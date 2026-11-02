import { useEffect, useRef, useState } from "react";
import { ShieldAlert } from "lucide-react";

/**
 * The MIRSAAD mark.
 *
 * `public/logo.png` is a placeholder until the real mark lands — until then
 * the image 404s and this falls back to the Shield tile, so a header never
 * shows a broken-image X. Once the file exists the fallback stops rendering
 * on its own; nothing else has to change.
 */
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  const [ok, setOk] = useState(true);
  const imgRef = useRef<HTMLImageElement>(null);

  // The page is server-rendered, so a missing logo.png can finish 404ing
  // before React hydrates and attaches onError — which would leave the
  // broken-image X this component exists to prevent. A failed image is
  // `complete` with no width, so re-check once on mount as well.
  useEffect(() => {
    const img = imgRef.current;
    if (img?.complete && img.naturalWidth === 0) setOk(false);
  }, []);

  if (!ok) {
    return (
      <span
        className={`grid ${className} shrink-0 place-items-center rounded-lg`}
        style={{ background: "var(--gradient-primary)" }}
        aria-hidden
      >
        <ShieldAlert className="h-4 w-4 text-primary-foreground" />
      </span>
    );
  }

  return (
    <img
      ref={imgRef}
      src="/logo.png"
      alt="MIRSAAD logo"
      className={`${className} shrink-0 rounded-lg object-contain`}
      onError={() => setOk(false)}
    />
  );
}
