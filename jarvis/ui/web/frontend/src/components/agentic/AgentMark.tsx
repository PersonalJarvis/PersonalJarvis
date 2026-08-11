import { useState } from "react";
import { SquareTerminal } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * What ground a brand file needs in order to be legible.
 *
 * Every mark that ships with the app was drawn for the dark product this
 * started as, and they are not all broken in the same way on paper — so one
 * blanket treatment cannot fix them (maintainer, 2026-08-11: a light-mode
 * restore screen showed seven terminals with no sign of which CLI each was
 * running).
 *
 * * `ink` — a single-colour silhouette; the file is one `fill="#F4F4F5"` and
 *   nothing about that colour is brand. It is drawn as a MASK over the current
 *   text ink, so it follows the theme by itself: the same near-white on the
 *   dark app, near-black on paper, from one asset and with no theme prop
 *   threaded through the tree.
 * * `dark` — a full-colour lockup whose own background is white or near-white
 *   (OpenCode's outer square; the body of the Kimi glyph). On paper those
 *   shapes ARE the paper, and the mark collapses to whichever fragment happens
 *   to be dark — OpenCode became an unlabelled grey dot. These keep their
 *   colours and get a constant dark canvas: the ground they were drawn on.
 * * `any` — a lockup that already carries its own ground (Z.ai is a dark tile
 *   with a white glyph) and reads on either theme untouched.
 */
type LogoGround = "ink" | "dark" | "any";

interface LogoAsset {
  url: string;
  ground: LogoGround;
}

const AGENT_LOGOS: Record<string, LogoAsset> = {
  claude: { url: "/provider-logos/claude.svg", ground: "ink" },
  codex: { url: "/provider-logos/openai.svg", ground: "ink" },
  glm: { url: "/agent-logos/zai.svg", ground: "any" },
  kimi: { url: "/agent-logos/kimi.svg", ground: "dark" },
  opencode: { url: "/agent-logos/opencode.svg", ground: "dark" },
};

interface AgentMarkProps {
  agent: string;
  label: string;
  className?: string;
  size?: "sm" | "md" | "lg";
  /**
   * An image to draw instead of the built-in table's — the mark a user uploaded
   * for a CLI of their own.
   *
   * Passed in rather than looked up, because this component cannot know it: the
   * table above is a fixed list of brands that ship with the app, and an entry
   * added at runtime has no place in it. An entry with no logo (and every
   * shipped one) leaves this undefined and the table decides as before.
   *
   * An upload is treated as `any`: we know nothing about the file, and a canvas
   * added on a guess could hide a mark that was perfectly fine on its own.
   */
  logoUrl?: string;
  /**
   * How much of a mark this is.
   *
   * `boxed` is the identity badge: a framed tile, for the places that are ABOUT
   * which agent this is — a pane header, an agent picker. `plain` is the same
   * glyph with the tile taken away, for a list of conversations, where the mark
   * is a hint beside a title rather than the subject of the row. A framed tile
   * repeated down forty rows turns a reading list into a grid of boxes.
   *
   * A `dark`-ground lockup is the one exception: it cannot be shown without
   * something to sit on, so it keeps a small canvas even when plain. Better a
   * chip in a list than a mark nobody can identify.
   */
  variant?: "boxed" | "plain";
}

/**
 * A registry-safe visual identity for a terminal agent.
 *
 * Known products use local, offline brand assets. Unknown registry entries get
 * a neutral monogram, so adding another CLI never produces a broken image. The
 * plain terminal is a capability rather than a brand and therefore uses the
 * system-terminal glyph.
 */
export function AgentMark({
  agent,
  label,
  className,
  size = "md",
  variant = "boxed",
  logoUrl,
}: AgentMarkProps) {
  /*
   * WHICH logo failed, not merely "one did".
   *
   * A boolean would stick: a user who replaces a broken upload with a working
   * one keeps seeing the monogram, because the component is the same instance
   * and its flag never clears. Storing the URL that failed makes the next one a
   * fresh attempt without an effect to keep in step.
   */
  const [failedLogo, setFailedLogo] = useState<string | null>(null);
  const asset: LogoAsset | undefined = logoUrl
    ? { url: logoUrl, ground: "any" }
    : AGENT_LOGOS[agent];
  const failed = failedLogo != null && failedLogo === asset?.url;
  const monogram = label.trim().slice(0, 2).toUpperCase() || "?";
  const plain = variant === "plain";
  const drawn = agent !== "shell" && asset && !failed;
  /*
   * A mask has no load event, so the failure path above cannot cover it. That
   * is not a gap: `ink` is only ever an asset that ships inside the bundle,
   * while the uploads the fallback exists for are always drawn as an image.
   */
  const inked = drawn && asset.ground === "ink";
  const canvased = drawn && asset.ground === "dark";
  const sizeClass = plain
    ? size === "sm"
      ? "h-4 w-4"
      : "h-5 w-5"
    : size === "sm"
      ? "h-7 w-7 rounded-[5px]"
      : size === "lg"
        ? "h-11 w-11 rounded-control"
        : "h-9 w-9 rounded-control";
  const glyphClass = plain
    ? "h-full w-full"
    : size === "sm"
      ? "h-3.5 w-3.5"
      : size === "lg"
        ? "h-6 w-6"
        : "h-5 w-5";

  return (
    <span
      data-testid={`agent-mark-${agent}`}
      /* Which file is being drawn and on what, stated on the element rather
         than inferred from it: an `ink` mark is a CSS mask, and a mask lives in
         a style property jsdom does not model, so a test that reached for the
         <img> would report "no logo" for the marks that work best. */
      data-logo={drawn ? asset.url : undefined}
      data-ground={asset?.ground ?? "none"}
      aria-hidden="true"
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden text-[9px] font-bold tracking-tight text-muted-foreground",
        // The tile, and the brightness that goes with being one. Without it the
        // glyph sits at the weight of the text it accompanies, which is the
        // point of `plain`.
        plain
          ? canvased
            ? "rounded-[4px] bg-scrim/95 p-px"
            : "opacity-70"
          : canvased
            ? "border border-border/80 bg-scrim/95"
            : "border border-border/80 bg-background/80",
        sizeClass,
        className,
      )}
    >
      {agent === "shell" ? (
        <SquareTerminal
          className={plain || size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"}
        />
      ) : inked ? (
        /*
         * `bg-foreground` rather than `bg-current`: the wrapper's ink is the
         * muted channel, which is right for the monogram beside a title and
         * wrong for a brand mark. Foreground is #F4F4F5 on the dark theme —
         * the exact colour the file was authored in, so the dark app is
         * pixel-identical to before this split existed.
         */
        <span
          className={cn("block bg-foreground", glyphClass)}
          style={{
            WebkitMaskImage: `url("${asset.url}")`,
            maskImage: `url("${asset.url}")`,
            WebkitMaskRepeat: "no-repeat",
            maskRepeat: "no-repeat",
            WebkitMaskPosition: "center",
            maskPosition: "center",
            WebkitMaskSize: "contain",
            maskSize: "contain",
          }}
        />
      ) : drawn ? (
        <img
          src={asset.url}
          alt=""
          // Plain fills its box: there is no tile to sit inside, so the glyph
          // IS the mark and shrinking it further would leave a speck.
          className={cn("block object-contain", glyphClass)}
          onError={() => setFailedLogo(asset.url)}
        />
      ) : (
        <span className="font-mono">{monogram}</span>
      )}
    </span>
  );
}
