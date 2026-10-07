import { memo, useInsertionEffect } from "react";
import { useThemeValue } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";
import { FILE_ICON_COLORS, fileIconToken } from "./fileIcon";
import { FILE_ICON_SPRITE } from "./fileIconSprite";

const SPRITE_ID = "jv-file-icon-sprite";
const SVG_NS = "http://www.w3.org/2000/svg";

/** Mount the shared symbol sheet once; every icon below only references it. */
function ensureSprite() {
  if (typeof document === "undefined" || document.getElementById(SPRITE_ID)) return;
  const sheet = document.createElementNS(SVG_NS, "svg");
  sheet.id = SPRITE_ID;
  sheet.setAttribute("aria-hidden", "true");
  sheet.setAttribute("width", "0");
  sheet.setAttribute("height", "0");
  sheet.style.position = "absolute";
  sheet.style.overflow = "hidden";
  sheet.style.pointerEvents = "none";
  sheet.innerHTML = FILE_ICON_SPRITE;
  document.body.prepend(sheet);
}

/** A coloured glyph for a file, chosen by its name and extension. */
export const FileTypeIcon = memo(function FileTypeIcon({ path, className }: { path: string; className?: string }) {
  useInsertionEffect(ensureSprite, []);
  const theme = useThemeValue();
  const token = fileIconToken(path);
  const [light, dark] = FILE_ICON_COLORS[token];
  return (
    <svg
      aria-hidden
      data-file-icon={token}
      viewBox="0 0 16 16"
      className={cn("h-4 w-4 shrink-0", className)}
      style={{ color: theme === "light" ? light : dark }}
    >
      <use href={`#jv-file-icon-${token}`} />
    </svg>
  );
});
