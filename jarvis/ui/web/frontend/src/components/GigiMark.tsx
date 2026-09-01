import gigiMark from "@/assets/jarvis-mark.png";
import { cn } from "@/lib/utils";

/**
 * The Gigi app mark: the desktop app icon itself, at any size.
 *
 * The tile — pool of light, ink Gigi, hairline edge — is baked into the PNG by
 * `scripts/make_gigi_app_icon.py`, so this renders no background, no radius and
 * no shadow of its own. Anything drawn here would sit behind an already masked
 * shape and show as a square.
 *
 * The image is imported rather than referenced as `/jarvis-gigi-256.png`, so
 * Vite fingerprints it: a public/ file keeps its name across every redraw and
 * browsers go on serving the cached one. Do not swap this for the live SVG
 * mascot in chrome; that one moves, this one identifies the app.
 */
export function GigiMark({
  size,
  className,
  alt = "",
}: {
  size: number;
  className?: string;
  alt?: string;
}) {
  return (
    <img
      src={gigiMark}
      width={size}
      height={size}
      alt={alt}
      className={cn("shrink-0 select-none", className)}
      style={{ width: size, height: size }}
      draggable={false}
    />
  );
}
