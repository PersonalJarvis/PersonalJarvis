import type { CSSProperties } from "react";
import {
  AppWindow,
  BookOpen,
  Compass,
  Keyboard,
  Brain,
  CalendarClock,
  Code2,
  Database,
  FileText,
  FolderOpen,
  Globe,
  Image,
  Mail,
  Monitor,
  MousePointer2,
  PenLine,
  Plug,
  Presentation,
  Search,
  ShieldCheck,
  Sparkles,
  Terminal,
  Users,
  Workflow,
  Wrench,
} from "lucide-react";
import { McpLogo } from "@/components/extensions/McpLogo";
import type { ToolCategory, ToolChoice } from "./toolChoices";

type Palette = readonly [string, string];
type Mark = "colour" | "mono" | "plate";
interface Brand {
  palette: Palette;
  aliases?: string[];
  mark?: Mark;
  asset?: string;
}

// Ink pairs retain each brand's hue with contrast on paper and charcoal.
// Original colour SVGs stay intact; monochrome marks use the text ink.
const NEUTRAL: Palette = ["#24292e", "#e6edf3"];
const BLUE: Palette = ["#1558b0", "#8ab4f8"];
const VIOLET: Palette = ["#6840bb", "#c4abff"];
const GREEN: Palette = ["#167344", "#79dca4"];
const BRANDS: Record<string, Brand> = {
  gmail: { palette: ["#b3261e", "#ff938a"], aliases: ["google-mail", "gws-gmail"] },
  github: { palette: NEUTRAL, mark: "mono", aliases: ["gh"] },
  google_drive: { palette: GREEN, aliases: ["google-drive", "gdrive", "gws-drive"] },
  google_calendar: { palette: BLUE, aliases: ["google-calendar", "gws-calendar"] },
  airtable: { palette: ["#96650b", "#efc561"] },
  asana: { palette: ["#be3652", "#ff99ac"] },
  cal_com: { palette: NEUTRAL, mark: "mono", aliases: ["cal.com", "cal-com"] },
  canva: { palette: ["#087f88", "#69d6dd"] },
  clickup: { palette: VIOLET },
  discord: { palette: ["#454abe", "#a8acff"] },
  dropbox: { palette: BLUE },
  higgsfield: { palette: ["#50700a", "#ccf052"] },
  home_assistant: { palette: BLUE, aliases: ["home-assistant", "homeassistant"] },
  linear: { palette: VIOLET },
  notion: { palette: NEUTRAL },
  slack: { palette: ["#8d367d", "#e6a0d6"] },
  spotify: { palette: GREEN },
  supabase: { palette: GREEN },
  telegram: { palette: BLUE },
  todoist: { palette: ["#b12d27", "#ff9c92"] },
  vercel: { palette: NEUTRAL, mark: "mono" },
  youtube_music: { palette: ["#bb2424", "#ff9494"], aliases: ["youtube-music", "ytmusic"] },
  stripe: { palette: VIOLET, mark: "mono" },
  cloudflare: { palette: ["#a84c14", "#ffb276"], mark: "mono", aliases: ["wrangler"] },
  figma: { palette: ["#9c3a28", "#ffa18e"], mark: "mono" },
  gitlab: { palette: ["#a8441a", "#ffad83"], mark: "mono" },
  docker: { palette: BLUE, mark: "mono" },
  n8n: { palette: ["#b43351", "#f69ab1"], mark: "mono" },
  postgresql: { palette: BLUE, aliases: ["postgres"] },
  obsidian: { palette: VIOLET, mark: "mono" },
  openai: { palette: NEUTRAL, mark: "mono", aliases: ["codex", "chatgpt"] },
  openrouter: { palette: NEUTRAL, mark: "mono" },
  claude: { palette: ["#a04f31", "#e6aa89"], aliases: ["anthropic", "claude-code"] },
  gemini: { palette: BLUE },
  "google-cloud": { palette: BLUE, aliases: ["gcloud", "vertex"] },
  antigravity: { palette: BLUE, aliases: ["agy"] },
  ollama: { palette: NEUTRAL, mark: "mono" },
  nvidia: { palette: GREEN },
  groq: { palette: ["#b14628", "#ffaa8e"], mark: "mono" },
  elevenlabs: { palette: NEUTRAL, mark: "mono" },
  cartesia: { palette: GREEN },
  inworld: { palette: VIOLET },
  xai: { palette: NEUTRAL, mark: "mono", aliases: ["grok", "grok-build"] },
  cursor: { palette: NEUTRAL, mark: "mono", asset: "/agent-logos/cursor.svg" },
  deepseek: {
    palette: BLUE,
    mark: "mono",
    asset: "/agent-logos/deepseek.svg",
    aliases: ["deepseek-harness"],
  },
  kimi: { palette: BLUE, mark: "plate", asset: "/agent-logos/kimi.svg" },
  opencode: { palette: NEUTRAL, mark: "plate", asset: "/agent-logos/opencode.svg" },
  zai: { palette: NEUTRAL, asset: "/agent-logos/zai.svg", aliases: ["glm", "z.ai"] },
};

const assets = import.meta.glob("../../assets/{brands,providers,tool-brands}/*.{svg,png}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

function assetFor(key: string): string | undefined {
  return (
    BRANDS[key]?.asset ||
    assets[`../../assets/brands/${key}.svg`] ||
    assets[`../../assets/tool-brands/${key}.svg`] ||
    assets[`../../assets/providers/${key}.svg`] ||
    assets[`../../assets/providers/${key}.png`]
  );
}

const normalize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, "");
const bundledKeys = Object.keys(assets).map((path) => path.split("/").pop()!.replace(/\.(svg|png)$/, ""));
const aliases = [...new Set([...Object.keys(BRANDS), ...bundledKeys])]
  .flatMap((key) =>
    [key, ...(BRANDS[key]?.aliases ?? [])].map((alias) => ({ key, alias: alias.replace(/[_. ]/g, "-") })),
  )
  .sort((a, b) => b.alias.length - a.alias.length);

function brandFor(value: string): string | undefined {
  // Identity segments only: "my-github-helper" is not GitHub and
  // "drive-jarvis" is not Drive. Descriptions never establish brand identity.
  for (const segment of value.replace(/__/g, ":").split(/[/:]/)) {
    const clean = segment
      .toLowerCase()
      .replace(/^(?:cli|mcp)[_-]/, "")
      .replace(/[-_](?:mcp|server|cli)$/, "")
      .replace(/[_. ]/g, "-");
    const exact = aliases.find((entry) => normalize(entry.alias) === normalize(clean));
    if (exact) return exact.key;
    const action = aliases.find((entry) => clean.startsWith(entry.alias + "-"));
    if (action) return action.key;
  }
  return undefined;
}

export const CATEGORY_ICONS = {
  plugins: Plug,
  skills: Sparkles,
  mcp: McpLogo,
  memory: Brain,
  web: Globe,
  files: FolderOpen,
  automation: CalendarClock,
  system: Wrench,
  cli: Terminal,
};
const CATEGORY_PALETTES: Record<ToolCategory, Palette> = {
  plugins: BLUE,
  skills: VIOLET,
  mcp: ["#127576", "#7ed5d0"],
  memory: VIOLET,
  web: BLUE,
  files: ["#8c610c", "#e3bf78"],
  automation: GREEN,
  system: NEUTRAL,
  cli: NEUTRAL,
};

const DETAIL_ICONS = [
  [/\b(wiki|recall|knowledge)\b/, BookOpen],
  [/\b(pdf|document|docs|word|read)\b/, FileText],
  [/\b(slides|presentation|powerpoint)\b/, Presentation],
  [/\b(image|design|photo|imagegen)\b/, Image],
  [/\b(search|find|research)\b/, Search],
  [/\b(email|mail)\b/, Mail],
  [/\b(contact|contacts|people)\b/, Users],
  [/\b(database|sql)\b/, Database],
  [/\b(security|audit)\b/, ShieldCheck],
  [/\b(code|coding|develop)\b/, Code2],
  [/\b(write|edit)\b/, PenLine],
  [/\b(click|mouse|pointer)\b/, MousePointer2],
  [/\b(screen|desktop|computer)\b/, Monitor],
  [/\b(workflow|automation)\b/, Workflow],
  [/\b(shell|terminal)\b/, Terminal],
  [/\b(type|hotkey|keyboard)\b/, Keyboard],
  [/\b(app)\b/, AppWindow],
  [/\b(navigate)\b/, Compass],
] as const;

export function toolIdentity(row: ToolChoice) {
  const key =
    (row.brand && brandFor(row.brand)) ||
    [row.id, row.group, row.skill, row.label].map((s) => brandFor(s || "")).find(Boolean);
  const brand = key ? BRANDS[key] : undefined;
  const palette = brand?.palette ?? CATEGORY_PALETTES[row.category];
  const words = `${row.id} ${row.skill} ${row.label}`.toLowerCase().replace(/[_:-]/g, " ");
  const Glyph =
    row.category === "mcp"
      ? McpLogo
      : (DETAIL_ICONS.find(([pattern]) => pattern.test(words))?.[1] ??
        CATEGORY_ICONS[row.category]);
  return {
    key,
    logo: key ? assetFor(key) : undefined,
    mark: brand?.mark ?? "colour",
    palette,
    Glyph,
  };
}

function wash(hex: string, opacity: number): string {
  const channels = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
  return `rgba(${channels.join(",")},${opacity})`;
}

export function toolIdentityStyle(row: ToolChoice): CSSProperties {
  const {
    palette: [light, dark],
  } = toolIdentity(row);
  return {
    "--tool-ink-light": light,
    "--tool-ink-dark": dark,
    "--tool-wash-light": wash(light, 0.07),
    "--tool-wash-dark": wash(dark, 0.09),
  } as CSSProperties;
}
