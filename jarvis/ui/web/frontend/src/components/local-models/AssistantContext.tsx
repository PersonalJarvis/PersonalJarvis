/**
 * The setup helper's context rail — who is answering, and what runs locally.
 *
 * The section is called "Local models", but the helper itself is NOT one: it
 * runs on the Agents-tier Tool Model, in the cloud, billed through that key
 * (`jarvis/local_models/assistant_session.py`). Every step it narrates
 * ("Downloaded ornith:9b") is about a model on this machine. Without naming
 * both, a reader cannot tell who is speaking from what is being installed —
 * so the rail names the brain at the top and the machine's roles underneath,
 * in that order.
 *
 * Nothing here is new data: the pair comes from the `/session` response the
 * panel already fetches, the roles and the server from the same two queries
 * the Overview tab runs (one React-Query cache, so switching tabs re-fetches
 * nothing).
 *
 * On a narrow window the rail is gone and `AssistantOriginChip` carries the
 * same answer in the header — one line instead of three blocks.
 */
import { useMemo } from "react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { StatusDot } from "@/components/extensions/primitives";
import { useRoles, useServer } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { AssistantHealth, AssistantSessionResponse } from "./assistantApi";
import { assistantTimestamp } from "./assistantApi";

/** The tone a health record draws with — shared by the rail and the timeline. */
export function healthTone(
  health: AssistantHealth | null,
): "ok" | "off" | "warn" | "error" {
  if (!health) return "off";
  if (health.status === "ok") return "ok";
  if (health.status === "unknown") return "off";
  return "warn";
}

/** "gemini" -> "Gemini" when nothing better is known; the resolver wins. */
export function providerDisplayName(
  providerId: string,
  resolve?: (id: string) => string,
): string {
  const resolved = resolve?.(providerId);
  if (resolved && resolved !== providerId) return resolved;
  return providerId ? providerId[0].toUpperCase() + providerId.slice(1) : "";
}

export interface AssistantContextProps {
  /** The local server's card id — the roles and server queries hang off it. */
  providerId: string;
  /** What the section calls that server ("Ollama"). */
  serverLabel: string;
  /** `/session`: the Agents-tier pair plus whether it can run right now. */
  session: AssistantSessionResponse | null;
  health: AssistantHealth | null;
  onOpenApiKeys: () => void;
  /** Starts a diagnose run — the Fix beside a failing health record. */
  onDiagnose: () => void;
  /** Provider id -> display label, from the section's provider list. */
  providerLabel?: (id: string) => string;
}

/** The one-line answer to "who is writing this?", for narrow windows. */
export function AssistantOriginChip({
  session,
  providerLabel,
  onOpenApiKeys,
}: {
  session: AssistantSessionResponse | null;
  providerLabel?: (id: string) => string;
  onOpenApiKeys: () => void;
}) {
  const t = useT();
  if (!session?.provider) return null;
  const label = providerDisplayName(session.provider, providerLabel);
  return (
    <button
      type="button"
      onClick={onOpenApiKeys}
      title={t("local_models.assistant.origin_cloud")}
      data-testid="assistant-origin-chip"
      className={cn(
        "inline-flex max-w-[18rem] items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1",
        "text-[11.5px] text-muted-foreground transition-colors hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
      )}
    >
      <ProviderLogo providerId={session.provider} label={label} size="sm" />
      <span className="truncate">
        {label}
        {session.model ? ` · ${session.model}` : ""}
      </span>
      {!session.ready && (
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden />
      )}
    </button>
  );
}

export function AssistantContext({
  providerId,
  serverLabel,
  session,
  health,
  onOpenApiKeys,
  onDiagnose,
  providerLabel,
}: AssistantContextProps) {
  const t = useT();
  const roles = useRoles(providerId);
  const server = useServer(providerId);

  // The primary slots only — the read-only advanced ones live in the Roles
  // panel behind "More roles" and would turn a rail into a ledger.
  const rows = useMemo(
    () => (roles.data?.roles ?? []).filter((r) => !r.advanced),
    [roles.data],
  );

  const checkedAt = assistantTimestamp(health?.checked_at);
  const needsFix = health?.status === "error" || health?.status === "needs_setup";
  const brandLabel = session?.provider
    ? providerDisplayName(session.provider, providerLabel)
    : "";

  return (
    <aside
      className="hidden w-[19rem] shrink-0 flex-col gap-7 overflow-y-auto border-l border-border/70 bg-card/30 px-5 py-6 xl:flex"
      data-testid="assistant-context"
    >
      {/* ── who is answering ─────────────────────────────────────────── */}
      <section data-testid="assistant-origin">
        <RailTitle>{t("local_models.assistant.origin_title")}</RailTitle>
        {brandLabel ? (
          <>
            <div className="mt-2.5 flex items-center gap-2">
              <ProviderLogo
                providerId={session?.provider ?? ""}
                label={brandLabel}
                size="sm"
              />
              <span className="truncate text-[13.5px] font-medium text-foreground">
                {brandLabel}
              </span>
            </div>
            <p
              className="mt-1 break-all font-mono text-[11.5px] leading-snug text-muted-foreground"
              data-testid="assistant-origin-model"
            >
              {session?.model || t("local_models.assistant.origin_default_model")}
            </p>
          </>
        ) : (
          <p className="mt-2.5 text-[12.5px] text-muted-foreground">
            {t("local_models.assistant.origin_unset")}
          </p>
        )}

        {session && !session.ready && session.reason ? (
          <p
            className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[12px] leading-relaxed"
            data-testid="assistant-origin-blocked"
          >
            {session.reason}
          </p>
        ) : (
          <p className="mt-3 text-[12px] leading-relaxed text-muted-foreground">
            {t("local_models.assistant.origin_cloud")}
          </p>
        )}

        <button
          type="button"
          onClick={onOpenApiKeys}
          className="mt-2 text-[12px] font-medium text-primary underline-offset-4 hover:underline"
          data-testid="assistant-origin-change"
        >
          {t("local_models.assistant.origin_change")}
        </button>
      </section>

      {/* ── what runs here ───────────────────────────────────────────── */}
      <section data-testid="assistant-machine">
        <RailTitle>{t("local_models.assistant.machine_title")}</RailTitle>
        <div className="mt-2.5">
          <StatusDot
            tone={server.data?.running ? "ok" : "off"}
            label={
              <span className="text-[12.5px]">
                {fill(
                  t(
                    server.data?.running
                      ? "local_models.assistant.machine_server_running"
                      : "local_models.assistant.machine_server_stopped",
                  ),
                  { server: serverLabel },
                )}
              </span>
            }
          />
        </div>

        {rows.length > 0 ? (
          <dl className="mt-3 flex flex-col gap-2">
            {rows.map((row) => {
              // Configured but gone from the server: the run will fail on it,
              // so it reads as a warning here rather than as a plain name.
              const missing = row.current !== "" && !row.installed;
              return (
                <div key={row.id} className="flex flex-col gap-0.5">
                  <dt className="text-[11.5px] text-muted-foreground">
                    {t(row.label_key)}
                  </dt>
                  <dd
                    className={cn(
                      "break-all font-mono text-[11.5px] leading-snug",
                      row.current ? "text-foreground" : "text-muted-foreground/70",
                      missing && "text-amber-500",
                    )}
                    data-testid={`assistant-machine-${row.id}`}
                  >
                    {row.current || t("local_models.assistant.machine_unset")}
                    {missing && (
                      <span className="ml-1 font-sans text-[11px]">
                        ({t("local_models.assistant.machine_missing")})
                      </span>
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
        ) : (
          <p className="mt-3 text-[12px] text-muted-foreground">
            {roles.isLoading
              ? t("local_models.assistant.machine_loading")
              : (roles.data?.error ?? "")}
          </p>
        )}
      </section>

      {/* ── the monitor's verdict, parked instead of scrolling away ──── */}
      {health && checkedAt && (
        <section data-testid="assistant-health-rail">
          <StatusDot
            tone={healthTone(health)}
            label={
              <span className="text-[12px] leading-relaxed">
                {fill(t("local_models.assistant.last_check"), {
                  when: checkedAt.toLocaleString(),
                  what:
                    health.reason ||
                    t(`local_models.assistant.health_${health.status}`),
                })}
              </span>
            }
          />
          {needsFix && (
            <button
              type="button"
              onClick={onDiagnose}
              className="mt-1.5 text-[12px] font-medium text-primary underline-offset-4 hover:underline"
              data-testid="assistant-health-rail-fix"
            >
              {t("local_models.assistant.fix")}
            </button>
          )}
        </section>
      )}
    </aside>
  );
}

function RailTitle({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/80">
      {children}
    </h4>
  );
}
