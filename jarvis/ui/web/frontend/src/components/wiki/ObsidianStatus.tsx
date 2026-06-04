/**
 * Small status pill that mirrors `GET /api/setup/obsidian/status`.
 *
 * Visible at the top of the Wiki tab; tells the user at a glance whether
 * Obsidian is connected to the on-disk vault. Five visual states:
 *
 *   - LOADING    grey, animated dots, no interaction
 *   - OK         green "Obsidian: verbunden", tooltip shows vault + version
 *   - REGISTER   orange "Obsidian: nicht registriert", click opens setup
 *   - INSTALL    yellow "Obsidian: nicht installiert", click opens setup
 *   - UNCLEAR    grey "Obsidian: Status unklar", click opens setup
 *
 * The click handler is intentionally minimal — Sub-Agent 5 wires the real
 * setup dialog in. This component only owns presentation + polling.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, AlertTriangle, Download, HelpCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ObsidianStatus as ObsidianStatusType } from "@/types/setup";

interface Props {
  /** Called when the user clicks a non-OK pill. */
  onOpenSetup: (status: ObsidianStatusType) => void;
  /** Optional fetch override — used by tests. Defaults to `globalThis.fetch`. */
  fetchImpl?: typeof fetch;
  /** Poll interval in ms. Defaults to 30 s. */
  pollIntervalMs?: number;
}

type Visual = "loading" | "ok" | "register" | "install" | "unclear";

const DEFAULT_POLL_MS = 30_000;
const STATUS_URL = "/api/setup/obsidian/status";

function classify(status: ObsidianStatusType | null, errored: boolean): Visual {
  if (errored) return "unclear";
  if (status === null) return "loading";
  if (status.note && status.note.trim().length > 0) return "unclear";
  if (!status.installed) return "install";
  if (!status.vault_registered) return "register";
  if (status.recommended_action === "ok") return "ok";
  // Any other recommended_action with installed=true + registered=true is unusual.
  return "unclear";
}

// Tailwind colour classes mirroring the codebase conventions
// (cf. PageHeader's TYPE_COLOR map and ObsidianButton's outline style).
// Hex fallbacks from the task brief: ok=#5bd4a4 register=#ffb84d install=#facc15 unclear=#8d94a8
const VISUAL_STYLE: Record<Visual, string> = {
  loading:
    "border-border bg-secondary/40 text-muted-foreground cursor-default",
  ok: "border-[#5bd4a4]/40 bg-[#5bd4a4]/10 text-[#5bd4a4] cursor-default",
  register:
    "border-[#ffb84d]/40 bg-[#ffb84d]/10 text-[#ffb84d] cursor-pointer hover:bg-[#ffb84d]/20",
  install:
    "border-[#facc15]/40 bg-[#facc15]/10 text-[#facc15] cursor-pointer hover:bg-[#facc15]/20",
  unclear:
    "border-[#8d94a8]/40 bg-[#8d94a8]/10 text-[#8d94a8] cursor-pointer hover:bg-[#8d94a8]/20",
};

const VISUAL_LABEL: Record<Visual, string> = {
  loading: "Obsidian: …",
  ok: "Obsidian: verbunden",
  register: "Obsidian: nicht registriert",
  install: "Obsidian: nicht installiert",
  unclear: "Obsidian: Status unklar",
};

function VisualIcon({ visual }: { visual: Visual }): JSX.Element {
  if (visual === "ok") {
    return <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />;
  }
  if (visual === "install") {
    return <Download className="h-3.5 w-3.5" aria-hidden />;
  }
  if (visual === "register") {
    return <AlertTriangle className="h-3.5 w-3.5" aria-hidden />;
  }
  if (visual === "unclear") {
    return <HelpCircle className="h-3.5 w-3.5" aria-hidden />;
  }
  // loading: three pulsing dots
  return (
    <span
      className="inline-flex items-center gap-0.5"
      aria-hidden
      data-testid="obsidian-status-spinner"
    >
      <span className="block h-1 w-1 animate-pulse rounded-full bg-current" />
      <span
        className="block h-1 w-1 animate-pulse rounded-full bg-current"
        style={{ animationDelay: "150ms" }}
      />
      <span
        className="block h-1 w-1 animate-pulse rounded-full bg-current"
        style={{ animationDelay: "300ms" }}
      />
    </span>
  );
}

export function ObsidianStatus({
  onOpenSetup,
  fetchImpl,
  pollIntervalMs = DEFAULT_POLL_MS,
}: Props): JSX.Element {
  const [status, setStatus] = useState<ObsidianStatusType | null>(null);
  const [errored, setErrored] = useState(false);
  // Initial fetch must show LOADING — track whether at least one fetch
  // attempt has resolved (success OR failure) before letting `errored`
  // collapse us into UNCLEAR. Otherwise a synchronous failing fetch in
  // tests would skip the LOADING phase.
  const [resolvedOnce, setResolvedOnce] = useState(false);
  // Stable ref for the fetch impl so the polling effect can swap it without
  // tearing down the interval.
  const fetchRef = useRef<typeof fetch>(fetchImpl ?? globalThis.fetch);
  useEffect(() => {
    fetchRef.current = fetchImpl ?? globalThis.fetch;
  }, [fetchImpl]);

  const doFetch = useCallback(async () => {
    try {
      const fn = fetchRef.current;
      const res = await fn(STATUS_URL, { method: "GET" });
      if (!res.ok) {
        setErrored(true);
        setStatus(null);
        return;
      }
      const json = (await res.json()) as ObsidianStatusType;
      setStatus(json);
      setErrored(false);
    } catch {
      setErrored(true);
      setStatus(null);
    } finally {
      setResolvedOnce(true);
    }
  }, []);

  useEffect(() => {
    void doFetch();
    const interval = window.setInterval(() => {
      void doFetch();
    }, pollIntervalMs);
    return () => {
      window.clearInterval(interval);
    };
  }, [doFetch, pollIntervalMs]);

  const visual: Visual = resolvedOnce ? classify(status, errored) : "loading";
  const label = VISUAL_LABEL[visual];

  const tooltip =
    visual === "ok" && status
      ? status.version
        ? `${status.vault_path} · Obsidian ${status.version}`
        : status.vault_path
      : visual === "loading"
        ? "Status wird geladen…"
        : status?.note
          ? status.note
          : "Klick für Setup";

  const handleClick = useCallback(() => {
    if (visual === "loading" || visual === "ok") return;
    if (!status && !errored) return;
    // Build a safe status object even if the fetch errored — the dialog
    // needs *something* to decide its initial step.
    const payload: ObsidianStatusType = status ?? {
      installed: false,
      version: null,
      config_exists: false,
      vault_registered: false,
      vault_path: "",
      recommended_action: "install_obsidian",
      note: "Status konnte nicht geladen werden.",
    };
    onOpenSetup(payload);
  }, [visual, status, errored, onOpenSetup]);

  const interactive = visual !== "ok" && visual !== "loading";

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={!interactive}
      title={tooltip}
      data-testid="obsidian-status-pill"
      data-visual={visual}
      aria-label={label}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
        "disabled:opacity-100",
        VISUAL_STYLE[visual],
      )}
    >
      <VisualIcon visual={visual} />
      <span>{label}</span>
    </button>
  );
}
