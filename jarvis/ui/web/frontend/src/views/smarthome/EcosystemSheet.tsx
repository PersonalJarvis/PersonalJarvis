/**
 * What opens when an ecosystem card is clicked — and what makes it a card
 * rather than a poster.
 *
 * The cards used to be plain `<div>`s with no handler at all: twenty tiles that
 * looked pressable and did nothing. Every one of them now answers the three
 * questions someone actually has, in the order they have them:
 *
 *   1. What of mine will show up?
 *   2. What do I have to DO?  — the numbered steps, in plain words
 *   3. How long does it last before I have to log in again?
 *
 * And then it offers the one action that is true for that ecosystem:
 *
 * * **Home Assistant** — the form itself, right here. Two fields. Sending
 *   someone to a different screen to paste a token was the complaint.
 * * **via a hub** — "set it up in Home Assistant", with the hub's own state
 *   folded in: connect Home Assistant first if it is not connected yet, because
 *   telling someone to open a hub they have not got is a dead end.
 * * **not possible** — no button at all, and the reason in plain words. A
 *   disabled button invites a click that can never work; a sentence does not.
 */
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ExternalLink, Loader2, Lock, ShieldCheck, X } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { connectHomeAssistant, type Ecosystem, type ProviderStatus } from "@/hooks/useSmartHome";
import { BrandMark } from "@/views/smarthome/BrandMark";

export interface EcosystemSheetProps {
  ecosystem: Ecosystem | null;
  onOpenChange: (open: boolean) => void;
  /** Home Assistant's live status — decides what a "via hub" entry can offer. */
  hubStatus: ProviderStatus | undefined;
  onConnected: () => void;
  /** Jump to the Home Assistant entry from a "connect the hub first" prompt. */
  onOpenHub: () => void;
}

export function EcosystemSheet({
  ecosystem,
  onOpenChange,
  hubStatus,
  onConnected,
  onOpenHub,
}: EcosystemSheetProps) {
  const t = useT();
  const open = ecosystem !== null;
  const hubConnected = hubStatus?.state === "connected";
  const isHub = ecosystem?.id === "home_assistant";
  const unreachable = ecosystem?.reachability === "unavailable";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="ecosystem-sheet"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(88dvh,46rem)] w-[min(560px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card shadow-[0_28px_90px_-24px_rgba(0,0,0,0.75)] outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          {ecosystem && (
            <>
              <header className="flex items-start gap-3 border-b border-border px-5 py-4">
                <BrandMark
                  id={ecosystem.id}
                  name={ecosystem.display_name}
                  logoSlug={ecosystem.logo_slug}
                  logoColor={ecosystem.logo_color}
                  size="md"
                  dimmed={unreachable}
                />
                <div className="min-w-0 flex-1">
                  <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                    {ecosystem.display_name}
                  </Dialog.Title>
                  <Dialog.Description className="mt-0.5 text-xs text-muted-foreground">
                    {ecosystem.covers !== "—"
                      ? ecosystem.covers
                      : t(`smarthome.reach.${ecosystem.reachability}`)}
                  </Dialog.Description>
                </div>
                <Dialog.Close
                  className="rounded-md p-1 text-muted-foreground transition-colors hover:text-foreground"
                  aria-label={t("common.close")}
                >
                  <X className="h-4 w-4" aria-hidden />
                </Dialog.Close>
              </header>

              <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {ecosystem.note}
                </p>

                {ecosystem.setup_steps.length > 0 && (
                  <section>
                    <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {t("smarthome.eco.steps")}
                    </h4>
                    <ol className="space-y-2">
                      {ecosystem.setup_steps.map((step, index) => (
                        <li key={step} className="flex gap-2.5 text-sm text-foreground">
                          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border text-[11px] tabular-nums text-muted-foreground">
                            {index + 1}
                          </span>
                          <span className="leading-relaxed">{step}</span>
                        </li>
                      ))}
                    </ol>
                  </section>
                )}

                <div className="flex flex-wrap gap-2">
                  <Fact
                    icon={<ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
                    label={t(`smarthome.longevity.${ecosystem.longevity}`)}
                  />
                  <Fact
                    icon={<Lock className="h-3.5 w-3.5" aria-hidden />}
                    label={t(`smarthome.conn.${ecosystem.connection}`)}
                    fallback={ecosystem.connection}
                  />
                </div>

                {isHub && <HubConnectForm onConnected={onConnected} />}

                {!isHub && !unreachable && !hubConnected && (
                  <div className="rounded-lg border border-primary/30 bg-primary/[0.06] p-3">
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t("smarthome.eco.needs_hub")}
                    </p>
                    <button
                      type="button"
                      onClick={onOpenHub}
                      data-testid="ecosystem-open-hub"
                      className="mt-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
                    >
                      {t("smarthome.eco.connect_hub_first")}
                    </button>
                  </div>
                )}

                {!isHub && !unreachable && hubConnected && (
                  <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/[0.07] p-3 text-xs leading-relaxed text-muted-foreground">
                    {t("smarthome.eco.hub_ready")}
                  </p>
                )}
              </div>

              <footer className="flex items-center gap-2 border-t border-border px-5 py-3">
                {ecosystem.docs_url && (
                  <a
                    href={ecosystem.docs_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-primary"
                  >
                    {t("smarthome.eco.docs")}
                    <ExternalLink className="h-3 w-3" aria-hidden />
                  </a>
                )}
                <div className="flex-1" />
                <Dialog.Close className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
                  {t("common.close")}
                </Dialog.Close>
              </footer>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Fact({
  icon,
  label,
  fallback,
}: {
  icon: React.ReactNode;
  label: string;
  fallback?: string;
}) {
  // `useT` hands back the key when a locale lacks it; showing a raw key would
  // be a visible bug, so an unknown value falls back to its own plain name.
  const text = fallback && label.startsWith("smarthome.") ? fallback : label;
  return (
    <span className="flex items-center gap-1.5 rounded-md border border-border bg-secondary/40 px-2 py-1 text-[11px] text-muted-foreground">
      {icon}
      {text}
    </span>
  );
}

/**
 * The two fields that connect a house.
 *
 * Address and token, nothing else. The token goes straight to the marketplace's
 * own credential endpoint, so this is the SAME connection the plugin surface
 * manages — not a second copy that could drift out of sync with it.
 */
function HubConnectForm({ onConnected }: { onConnected: () => void }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    setError("");
    setDone(false);
  }, [url, token]);

  const canSubmit = url.trim().length > 0 && token.trim().length > 0 && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setError("");
    try {
      await connectHomeAssistant(url.trim(), token.trim());
      setDone(true);
      setToken("");
      onConnected();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      data-testid="hub-connect-form"
      className="space-y-3 rounded-lg border border-border bg-secondary/25 p-3"
    >
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("smarthome.eco.connect_here")}
      </h4>
      <label className="block">
        <span className="mb-1 block text-xs text-muted-foreground">
          {t("smarthome.eco.field_address")}
        </span>
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="http://192.168.1.20:8123"
          spellCheck={false}
          autoComplete="off"
          data-testid="hub-address"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground/70 focus:border-primary/50"
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-xs text-muted-foreground">
          {t("smarthome.eco.field_token")}
        </span>
        {/* type=password so a long-lived token is not left readable on a
            screen someone is sharing — it is a ten-year credential. */}
        <input
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="eyJhbGciOi…"
          spellCheck={false}
          autoComplete="off"
          data-testid="hub-token"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground/70 focus:border-primary/50"
        />
      </label>
      {error && (
        <p data-testid="hub-connect-error" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {done && (
        <p className="text-xs text-emerald-500">{t("smarthome.eco.connected_ok")}</p>
      )}
      <button
        type="button"
        disabled={!canSubmit}
        onClick={() => void submit()}
        data-testid="hub-connect-submit"
        className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-3 py-2 text-xs font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
      >
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
        {t("smarthome.connections.connect")}
      </button>
    </section>
  );
}
