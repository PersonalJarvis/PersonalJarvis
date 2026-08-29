import { HardDrive, Server } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The brand mark on a provider row.
 *
 * Every card that talks to the same company draws the same mark: the Gemini
 * spark on the Gemini brain, STT, TTS and Live cards alike, the OpenAI knot on
 * the OpenAI, Whisper, Realtime and Codex cards. The mapping therefore goes
 * provider id -> FAMILY -> file, and the family table is the only place a new
 * provider needs an entry. Presentation only: nothing here decides behaviour
 * (AP-21) — a provider with no row in the table still works, it just draws a
 * neutral glyph.
 *
 * Three render paths, recorded per file in `src/assets/providers/LOGOS.md`:
 * `colour` is the vendor's full-colour mark as an <img>; `mono` is a
 * single-colour glyph drawn as a CSS mask over the current text ink so it
 * follows the theme by itself; `own` is a lockup that carries its own ground
 * (Cartesia's green square) and is drawn untouched.
 */
type Render = "colour" | "mono" | "own";

interface FamilyLogo {
  file: string;
  render: Render;
}

const PROVIDER_FAMILY_LOGOS: Record<string, FamilyLogo> = {
  antigravity: { file: "antigravity.svg", render: "colour" },
  cartesia: { file: "cartesia.svg", render: "own" },
  claude: { file: "claude.svg", render: "colour" },
  elevenlabs: { file: "elevenlabs.svg", render: "mono" },
  gemini: { file: "gemini.svg", render: "colour" },
  "google-cloud": { file: "google-cloud.svg", render: "colour" },
  groq: { file: "groq.svg", render: "mono" },
  inworld: { file: "inworld.png", render: "own" },
  nvidia: { file: "nvidia.svg", render: "colour" },
  ollama: { file: "ollama.svg", render: "mono" },
  openai: { file: "openai.svg", render: "mono" },
  openrouter: { file: "openrouter.svg", render: "mono" },
  xai: { file: "xai.svg", render: "mono" },
};

// Bundled at build time (hashed URLs), so a mark renders offline and never
// calls a third party. The glob keeps the folder the single source of truth:
// dropping a file in and adding a family row is the whole wiring.
const BUNDLED = import.meta.glob("../../assets/providers/*.{svg,png}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

function assetUrl(file: string): string | undefined {
  return BUNDLED[`../../assets/providers/${file}`];
}

/**
 * Which family a provider id belongs to.
 *
 * Ids are catalog strings like "gemini-live", "vertex-stt", "openai-realtime"
 * or "claude-cli"; the family is the brand the card connects to, so the test
 * is "does the id name that company", in a fixed order where two could match
 * (a "vertex" card is Google Cloud, never Gemini, even though Vertex serves
 * Gemini models).
 */
export function providerFamily(providerId: string): string | null {
  const id = providerId.toLowerCase();
  if (id.includes("vertex")) return "google-cloud";
  if (id.includes("antigravity") || id === "agy-cli") return "antigravity";
  if (id.includes("gemini")) return "gemini";
  if (id.includes("codex") || id.startsWith("openai")) return "openai";
  if (id.includes("claude")) return "claude";
  if (id.includes("grok")) return "xai";
  if (id.includes("openrouter")) return "openrouter";
  if (id.includes("groq")) return "groq";
  if (id.includes("ollama")) return "ollama";
  if (id.includes("elevenlabs")) return "elevenlabs";
  if (id.includes("cartesia")) return "cartesia";
  if (id.includes("inworld")) return "inworld";
  if (id.includes("nvidia") || id.includes("nemotron")) return "nvidia";
  return null;
}

/** An on-device engine or a self-hosted server: a capability, not a brand. */
function localGlyph(providerId: string): "device" | "server" | null {
  const id = providerId.toLowerCase();
  if (id.startsWith("local-")) return "server";
  if (id.includes("faster-whisper") || id.includes("piper")) return "device";
  return null;
}

export function ProviderLogo({
  providerId,
  label,
  className,
  size = "md",
}: {
  providerId: string;
  /** The card's display name — its initial is the monogram fallback. */
  label: string;
  className?: string;
  /**
   * `md` is the provider-row tile; `sm` is the same mark drawn inline — no
   * tile, no border — for a menu row or a composer pill where the mark is a
   * hint beside a word rather than the subject of a card.
   */
  size?: "md" | "sm";
}) {
  const family = providerFamily(providerId);
  const entry = family ? PROVIDER_FAMILY_LOGOS[family] : undefined;
  const url = entry ? assetUrl(entry.file) : undefined;
  const local = localGlyph(providerId);
  const monogram = label.trim().slice(0, 1).toUpperCase() || "?";
  const small = size === "sm";
  const markClass = small ? "h-3.5 w-3.5" : "h-5 w-5";
  const glyphClass = small ? "h-3.5 w-3.5" : "h-4 w-4";

  return (
    <span
      aria-hidden="true"
      data-testid={`provider-logo-${providerId}`}
      data-family={family ?? undefined}
      className={cn(
        // One neutral tile for every mark, so a row of providers reads as one
        // list rather than a row of differently shaped app icons.
        small
          ? "inline-flex h-4 w-4 shrink-0 items-center justify-center overflow-hidden rounded-sm"
          : "inline-flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-surface border border-border/80 bg-background/70",
        className,
      )}
    >
      {url && entry?.render === "mono" ? (
        <span
          className={cn("block bg-foreground", markClass)}
          style={{
            WebkitMaskImage: `url("${url}")`,
            maskImage: `url("${url}")`,
            WebkitMaskRepeat: "no-repeat",
            maskRepeat: "no-repeat",
            WebkitMaskPosition: "center",
            maskPosition: "center",
            WebkitMaskSize: "contain",
            maskSize: "contain",
          }}
        />
      ) : url && entry?.render === "own" ? (
        // A lockup with its own ground fills the tile; its corners are ours.
        <img src={url} alt="" className="block h-full w-full object-cover" />
      ) : url ? (
        <img src={url} alt="" className={cn("block object-contain", markClass)} />
      ) : local === "server" ? (
        <Server className={cn("text-muted-foreground", glyphClass)} />
      ) : local === "device" ? (
        <HardDrive className={cn("text-muted-foreground", glyphClass)} />
      ) : (
        <span className={cn("font-display font-semibold text-muted-foreground", small ? "text-[10px]" : "text-xs")}>
          {monogram}
        </span>
      )}
    </span>
  );
}
