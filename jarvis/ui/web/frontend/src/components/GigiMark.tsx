import { useState } from "react";

import { cn } from "@/lib/utils";

const LOGO_RETRY_MAX = 5;
const LOGO_RETRY_BASE_MS = 1500;
const MARK_SRC = "/jarvis-gigi-256.png";

/**
 * The Gigi app mark: the desktop app icon itself, at any size.
 *
 * The tile — ink squircle, paper ghost, hairline edge — is baked into the PNG
 * by `scripts/make_gigi_app_icon.py`, so this renders no background, no radius
 * and no shadow of its own. Anything drawn here would sit behind an already
 * masked shape and show as a square. Do not swap this for the live SVG mascot
 * in chrome; that one moves, this one identifies the app.
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
  const [retry, setRetry] = useState(0);
  return (
    <img
      src={retry === 0 ? MARK_SRC : `${MARK_SRC}?retry=${retry}`}
      width={size}
      height={size}
      alt={alt}
      className={cn("shrink-0 select-none", className)}
      style={{ width: size, height: size }}
      draggable={false}
      onError={() => {
        if (retry < LOGO_RETRY_MAX) {
          window.setTimeout(() => setRetry((n) => n + 1), (retry + 1) * LOGO_RETRY_BASE_MS);
        }
      }}
    />
  );
}
