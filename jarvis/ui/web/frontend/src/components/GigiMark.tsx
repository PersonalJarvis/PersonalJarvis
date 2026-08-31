import { useState } from "react";

import { cn } from "@/lib/utils";

const LOGO_RETRY_MAX = 5;
const LOGO_RETRY_BASE_MS = 1500;

/**
 * The Gigi app mark: original black-and-white ghost on a white rounded tile.
 *
 * Same treatment as the desktop app icon — original PNG, uninverted, sitting
 * on a white squircle. Do not swap this for the live SVG mascot in chrome.
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
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden bg-white",
        "shadow-[0_1px_2px_rgba(0,0,0,0.22)]",
        className,
      )}
      style={{ width: size, height: size, borderRadius: "22%" }}
    >
      <img
        src={retry === 0 ? "/jarvis-logo.png" : `/jarvis-logo.png?retry=${retry}`}
        width={Math.round(size * 0.78)}
        height={Math.round(size * 0.78)}
        alt={alt}
        className="h-[78%] w-[78%] select-none"
        draggable={false}
        onError={() => {
          if (retry < LOGO_RETRY_MAX) {
            window.setTimeout(() => setRetry((n) => n + 1), (retry + 1) * LOGO_RETRY_BASE_MS);
          }
        }}
      />
    </span>
  );
}
