/**
 * Tool name → icon + color + display label.
 *
 * The heuristic first looks at the tool name (run_shell, search_web, ...)
 * and then refines based on args_preview (open_app chrome → Chrome icon,
 * dispatch_to_harness jarvis-agent → Jarvis-Agent). This gives us a
 * recognizable, brand-like look without an extra icon package.
 */
import {
  AppWindow,
  Bot,
  Brain,
  Camera,
  Chrome,
  Code2,
  FileText,
  Folder,
  Github,
  Globe,
  Keyboard,
  type LucideIcon,
  MessageSquare,
  Music,
  Play,
  Search,
  Server,
  Terminal,
  Users,
  Youtube,
} from "lucide-react";

import { agentBrandNow } from "@/lib/agentBrand";

export interface ToolAppearance {
  Icon: LucideIcon;
  label: string;
  /** Tailwind classes for the node background + border. */
  bg: string;
  ring: string;
  iconColor: string;
}

const DEFAULT: ToolAppearance = {
  Icon: Play,
  label: "Tool",
  bg: "bg-zinc-800",
  ring: "ring-zinc-600",
  iconColor: "text-zinc-300",
};

function matches(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle);
}

/**
 * Returns icon/label/color for a tool, based on the name + argument preview.
 */
export function getToolAppearance(
  toolName: string,
  argsPreview: string,
): ToolAppearance {
  const tool = toolName.toLowerCase();
  const args = (argsPreview || "").toLowerCase();

  // ── Shell / Code ───────────────────────────────────────────────
  if (tool === "run_shell" || tool === "bash" || tool === "run-shell") {
    return {
      Icon: Terminal,
      label: "Shell",
      bg: "bg-zinc-900",
      ring: "ring-zinc-600",
      iconColor: "text-emerald-300",
    };
  }

  if (tool === "dispatch_to_harness" || tool === "dispatch-to-harness") {
    if (matches(args, "openclaw")) {
      return {
        Icon: Code2,
        label: agentBrandNow(),
        bg: "bg-orange-950",
        ring: "ring-orange-500",
        iconColor: "text-orange-300",
      };
    }
    if (matches(args, "codex")) {
      return {
        Icon: Code2,
        label: "Codex",
        bg: "bg-emerald-950",
        ring: "ring-emerald-500",
        iconColor: "text-emerald-300",
      };
    }
    return {
      Icon: Server,
      label: "Harness",
      bg: "bg-emerald-950",
      ring: "ring-emerald-500",
      iconColor: "text-emerald-300",
    };
  }

  // ── Multi / Sub-Spawn ──────────────────────────────────────────
  if (tool === "multi_spawn" || tool === "multi-spawn") {
    return {
      Icon: Users,
      label: "Multi-Spawn",
      bg: "bg-purple-950",
      ring: "ring-purple-500",
      iconColor: "text-purple-300",
    };
  }

  if (tool === "spawn_openclaw" || tool === "spawn-openclaw") {
    return {
      Icon: Brain,
      label: agentBrandNow(),
      bg: "bg-violet-950",
      ring: "ring-violet-500",
      iconColor: "text-violet-300",
    };
  }

  // ── Screenshot / Vision ────────────────────────────────────────
  if (
    tool === "screenshot" ||
    tool === "screen_snapshot" ||
    tool === "screen-snapshot"
  ) {
    return {
      Icon: Camera,
      label: "Screenshot",
      bg: "bg-pink-950",
      ring: "ring-pink-500",
      iconColor: "text-pink-300",
    };
  }

  // ── Web Search (Google-ish) ────────────────────────────────────
  if (tool === "search_web" || tool === "search-web") {
    return {
      Icon: Search,
      label: "Web Search",
      bg: "bg-blue-950",
      ring: "ring-blue-500",
      iconColor: "text-blue-300",
    };
  }

  // ── Open-App mit Brand-Heuristik ───────────────────────────────
  if (tool === "open_app" || tool === "open-app") {
    if (matches(args, "chrome") || matches(args, "browser")) {
      return {
        Icon: Chrome,
        label: "Chrome",
        bg: "bg-blue-950",
        ring: "ring-blue-400",
        iconColor: "text-blue-300",
      };
    }
    if (matches(args, "youtube")) {
      return {
        Icon: Youtube,
        label: "YouTube",
        bg: "bg-red-950",
        ring: "ring-red-500",
        iconColor: "text-red-400",
      };
    }
    if (matches(args, "spotify") || matches(args, "music")) {
      return {
        Icon: Music,
        label: "Spotify",
        bg: "bg-emerald-950",
        ring: "ring-emerald-500",
        iconColor: "text-emerald-400",
      };
    }
    if (matches(args, "github")) {
      return {
        Icon: Github,
        label: "GitHub",
        bg: "bg-zinc-950",
        ring: "ring-zinc-400",
        iconColor: "text-zinc-200",
      };
    }
    if (matches(args, "notepad") || matches(args, ".txt") || matches(args, ".md")) {
      return {
        Icon: FileText,
        label: "Notepad",
        bg: "bg-sky-950",
        ring: "ring-sky-500",
        iconColor: "text-sky-300",
      };
    }
    if (matches(args, "explorer") || matches(args, "folder")) {
      return {
        Icon: Folder,
        label: "Explorer",
        bg: "bg-amber-950",
        ring: "ring-amber-500",
        iconColor: "text-amber-300",
      };
    }
    if (matches(args, "http") || matches(args, "www") || matches(args, ".com")) {
      return {
        Icon: Globe,
        label: "Web",
        bg: "bg-blue-950",
        ring: "ring-blue-500",
        iconColor: "text-blue-300",
      };
    }
    return {
      Icon: AppWindow,
      label: "Open App",
      bg: "bg-indigo-950",
      ring: "ring-indigo-500",
      iconColor: "text-indigo-300",
    };
  }

  // ── Input / Memory / Misc ──────────────────────────────────────
  if (tool === "type_text" || tool === "type-text") {
    return {
      Icon: Keyboard,
      label: "Type Text",
      bg: "bg-slate-900",
      ring: "ring-slate-500",
      iconColor: "text-slate-300",
    };
  }

  if (tool === "remember") {
    return {
      Icon: MessageSquare,
      label: "Remember",
      bg: "bg-amber-950",
      ring: "ring-amber-500",
      iconColor: "text-amber-300",
    };
  }

  if (tool === "whoami") {
    return {
      Icon: Bot,
      label: "Whoami",
      bg: "bg-cyan-950",
      ring: "ring-cyan-500",
      iconColor: "text-cyan-300",
    };
  }

  if (tool === "dispatch_to_admin" || tool === "dispatch-to-admin") {
    return {
      Icon: Server,
      label: "Admin Op",
      bg: "bg-rose-950",
      ring: "ring-rose-500",
      iconColor: "text-rose-300",
    };
  }

  return {
    ...DEFAULT,
    label: toolName,
  };
}
