/**
 * Small status pill that mirrors `GET /api/setup/obsidian/status`.
 *
 * Visible at the top of the Wiki tab; tells the user at a glance whether
 * Obsidian is connected to the on-disk vault. Five visual states:
 *
 *   - LOADING    grey, animated dots, no interaction
 *   - OK         green "Obsidian: connected", tooltip shows vault + version
 *   - REGISTER   orange "Obsidian: not registered", click opens setup
 *   - INSTALL    yellow "Obsidian: not installed", click opens setup
 *   - UNCLEAR    grey "Obsidian: status unclear", click opens setup
 *
 * The click handler is intentionally minimal — Sub-Agent 5 wires the real
 * setup dialog in. This component only owns presentation + polling.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, AlertTriangle, Download, HelpCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
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

const VISUAL_STYLE: Record<Visual, string> = {
  // Theme tokens only: status hues for status, neutral ink otherwise. The
  // connected state is quiet — a green icon, not a green slab.
  loading: "text-muted-foreground cursor-default",
  ok: "text-muted-foreground cursor-default [&>svg]:text-success",
  register: "text-warning cursor-pointer hover:bg-warning/10",
  install: "text-warning cursor-pointer hover:bg-warning/10",
  unclear: "text-muted-foreground cursor-pointer hover:bg-secondary hover:text-foreground",
};

function visualLabels(t: (key: string) => string): Record<Visual, string> {
  return {
    loading: t("obsidian_status.loading"),
    ok: t("obsidian_status.connected"),
    register: t("obsidian_status.not_registered"),
    install: t("obsidian_status.not_installed"),
    unclear: t("obsidian_status.unclear"),
  };
}

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
  const t = useT();
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
  const label = visualLabels(t)[visual];

  const tooltip =
    visual === "ok" && status
      ? status.version
        ? `${status.vault_path} · Obsidian ${status.version}`
        : status.vault_path
      : visual === "loading"
        ? t("obsidian_status.loading_tooltip")
        : status?.note
          ? status.note
          : t("obsidian_status.click_for_setup");

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
      note: t("obsidian_status.load_failed_note"),
    };
    onOpenSetup(payload);
  }, [visual, status, errored, onOpenSetup, t]);

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
        "inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium transition-colors",
        "disabled:opacity-100",
        VISUAL_STYLE[visual],
      )}
    >
      <VisualIcon visual={visual} />
      <span>{label}</span>
    </button>
  );
}
