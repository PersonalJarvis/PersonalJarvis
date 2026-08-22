import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  Github,
  Loader2,
  LogOut,
  ShieldCheck,
  Store,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { robustCopy } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// One identity for everything the app publishes.
//
// Packages and wallpapers are two lanes with two endpoints, but a person signs
// in once — so the sign-in lives here rather than inside either surface. Every
// surface reads the same query key, which means signing in from the wallpaper
// picker leaves the marketplace signed in and the other way round, with no
// state to keep in step.
//
// The flow is GitHub's DEVICE flow: the app shows a code, the user types it at
// github.com/login/device, and the app polls until GitHub reports approval.
// That is what lets a downloadable program authenticate at all — it holds no
// client secret, so the redirect flow the website uses is not available to it.
// The scope list is empty: the sign-in proves who someone is and grants
// nothing on their account.
// ---------------------------------------------------------------------------

export interface PublishIdentityWire {
  /** Package publishing (plugins and skills) is configured. */
  enabled: boolean;
  /** The wallpaper lane is configured — a fork may run one without the other. */
  wallpapers_enabled?: boolean;
  signed_in: boolean;
  login?: string;
  avatar_url?: string | null;
  /** Set when GitHub could not be reached — NOT the same as signed out. */
  unreachable?: string;
}

interface SigninStartWire {
  flow_id: string;
  user_code: string;
  verification_uri?: string | null;
  verification_uri_complete?: string | null;
  interval?: number;
}

export const PUBLISH_IDENTITY_KEY = ["marketplace-publish-identity"] as const;

const GITHUB_DEVICE_URL = "https://github.com/login/device";

async function fetchIdentity(): Promise<PublishIdentityWire> {
  const res = await fetch("/api/marketplace/publish/identity", { cache: "no-store" });
  if (!res.ok) throw new Error(`Identity request failed (${res.status})`);
  return res.json();
}

/** Who is signed in, shared by every surface that publishes. */
export function usePublishIdentity() {
  return useQuery({ queryKey: PUBLISH_IDENTITY_KEY, queryFn: fetchIdentity });
}

/**
 * The device-flow state machine: start → poll → connected | error, plus
 * cancel and sign-out. One hook so the header chip, the sign-in dialog and
 * the compact card inside a publish form all drive the same flow.
 */
export function useGithubSignIn() {
  const queryClient = useQueryClient();
  const [flow, setFlow] = useState<SigninStartWire | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [justConnected, setJustConnected] = useState(false);

  const start = useMutation({
    mutationFn: async (): Promise<SigninStartWire> => {
      const res = await fetch("/api/marketplace/publish/signin/start", { method: "POST" });
      if (!res.ok) {
        const detail = await res
          .json()
          .then((b: { detail?: string }) => b.detail)
          .catch(() => undefined);
        throw new Error(detail ?? `Sign-in start failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: (f) => {
      setFlow(f);
      setError(null);
      setJustConnected(false);
    },
    onError: (err: Error) => setError(err.message),
  });

  const signOut = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/marketplace/publish/identity", { method: "DELETE" });
      if (!res.ok) throw new Error(`Sign-out failed (${res.status})`);
      return res.json();
    },
    onSuccess: () => {
      setJustConnected(false);
      queryClient.invalidateQueries({ queryKey: PUBLISH_IDENTITY_KEY });
    },
  });

  // Poll the running flow until GitHub reports approval or an error.
  useEffect(() => {
    if (!flow) return;
    const intervalMs = Math.max(2, flow.interval ?? 5) * 1000;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/marketplace/publish/signin/poll/${flow.flow_id}`, {
          cache: "no-store",
        });
        if (res.status === 404) {
          setFlow(null);
          return;
        }
        const data = (await res.json()) as { status: string; error?: string };
        if (data.status === "connected") {
          setFlow(null);
          setJustConnected(true);
          queryClient.invalidateQueries({ queryKey: PUBLISH_IDENTITY_KEY });
        } else if (data.status === "error") {
          setFlow(null);
          setError(data.error ?? "sign-in failed");
        }
      } catch {
        // Network blip — keep polling until the flow expires.
      }
    }, intervalMs);
    return () => clearInterval(timer);
  }, [flow, queryClient]);

  const cancel = useCallback(() => {
    if (!flow) return;
    // Tell the backend to drop the flow, then forget it locally either way —
    // an already-finished flow 404s harmlessly.
    void fetch(`/api/marketplace/publish/signin/${flow.flow_id}`, { method: "DELETE" }).catch(
      () => undefined,
    );
    setFlow(null);
  }, [flow]);

  return {
    flow,
    error,
    justConnected,
    starting: start.isPending,
    signingOut: signOut.isPending,
    start: () => start.mutate(),
    cancel,
    signOut: () => signOut.mutate(),
    clearError: () => setError(null),
  };
}

/** Split "ABCD-1234" into its two halves for the ticket layout. */
function codeHalves(code: string): [string, string] {
  const idx = code.indexOf("-");
  if (idx === -1) return [code, ""];
  return [code.slice(0, idx), code.slice(idx + 1)];
}

/**
 * The GitHub avatar, with a letter tile behind it for the moment before the
 * picture arrives — and forever, if GitHub's CDN is unreachable.
 */
export function PublisherAvatar({
  login,
  url,
  size = 28,
  className,
}: {
  login?: string;
  url?: string | null;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const initial = (login ?? "?").slice(0, 1).toUpperCase();
  return (
    <span
      className={cn(
        "relative grid shrink-0 place-items-center overflow-hidden rounded-full",
        "bg-gradient-to-br from-primary/70 to-primary/30 text-primary-foreground",
        "ring-2 ring-background",
        className,
      )}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <span className="text-[11px] font-bold">{initial}</span>
      {url && !failed && (
        <img
          src={url}
          alt=""
          onError={() => setFailed(true)}
          className="absolute inset-0 h-full w-full object-cover"
        />
      )}
    </span>
  );
}

/**
 * The device-code ticket. Big code, one button that opens GitHub, a copy
 * button, and a pulse while the app waits — the whole sign-in on one card.
 * Shared by the dialog and the inline card so the flow looks identical
 * wherever it is started.
 */
function DeviceCodeTicket({
  flow,
  onCancel,
}: {
  flow: SigninStartWire;
  onCancel: () => void;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
  const [left, right] = codeHalves(flow.user_code);
  const verifyUrl = flow.verification_uri ?? GITHUB_DEVICE_URL;
  return (
    <div
      className="relative isolate overflow-hidden rounded-2xl border border-primary/30 bg-gradient-to-br from-primary/10 via-card to-card p-5"
      data-testid="device-code-ticket"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -z-10 -right-16 -top-16 h-48 w-48 rounded-full bg-primary/15 blur-3xl"
      />
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
        {t("marketplace.identity_code_label")}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <div
          className="flex items-center gap-2 font-mono text-3xl font-semibold tracking-[0.2em] text-foreground"
          data-testid="device-code"
          aria-label={flow.user_code}
        >
          <span>{left}</span>
          {right && (
            <>
              <span className="text-primary/60">–</span>
              <span>{right}</span>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={async () => {
            if (await robustCopy(flow.user_code)) setCopied(true);
          }}
          className="grid h-9 w-9 place-items-center rounded-lg border border-border bg-background/60 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          title={copied ? t("marketplace.identity_copied") : t("marketplace.identity_copy_code")}
          aria-label={t("marketplace.identity_copy_code")}
        >
          {copied ? <Check className="h-4 w-4 text-primary" /> : <Copy className="h-4 w-4" />}
        </button>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
        {t("marketplace.identity_code_hint")}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => openExternalUrl(verifyUrl)}>
          <Github className="mr-1.5 h-3.5 w-3.5" />
          {t("marketplace.identity_open_github")}
          <ExternalLink className="ml-1.5 h-3 w-3" />
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          {t("marketplace.identity_cancel")}
        </Button>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
          </span>
          {t("marketplace.identity_waiting")}
        </span>
      </div>
    </div>
  );
}

/**
 * The full sign-in dialog: what the sign-in is (and is not), then the ticket.
 * Opened from the marketplace header and from any surface that needs an
 * identity before it can continue.
 */
export function GithubSignInDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  useLocaleChunk("marketplace");
  const identity = usePublishIdentity();
  const signIn = useGithubSignIn();
  const startedRef = useRef(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Auto-start once: the person clicked "Sign in", so the code is what they
  // came for — not a second button that says "sign in" again.
  useEffect(() => {
    if (startedRef.current) return;
    if (identity.data?.signed_in) return;
    startedRef.current = true;
    signIn.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity.data?.signed_in]);

  const signedIn = identity.data?.signed_in === true;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("marketplace.identity_dialog_title")}
      data-testid="github-signin-dialog"
      className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 p-6 backdrop-blur-sm"
    >
      <div className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-2xl">
        <header className="flex items-start gap-3 border-b border-border px-5 py-4">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-foreground text-background">
            <Github className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-base font-semibold tracking-tight text-foreground">
              {t("marketplace.identity_dialog_title")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("marketplace.identity_dialog_subtitle")}
            </p>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label={t("marketplace.close")}
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="space-y-4 px-5 py-5">
          {signedIn ? (
            <div className="flex items-center gap-3 rounded-xl border border-primary/30 bg-primary/5 p-4">
              <PublisherAvatar
                login={identity.data?.login}
                url={identity.data?.avatar_url}
                size={40}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-foreground">
                  {fill(t("marketplace.identity_signed_in_as"), {
                    login: identity.data?.login ?? "",
                  })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("marketplace.identity_signed_in_hint")}
                </p>
              </div>
              <Check className="h-5 w-5 text-primary" />
            </div>
          ) : signIn.flow ? (
            <DeviceCodeTicket flow={signIn.flow} onCancel={signIn.cancel} />
          ) : (
            <div className="flex items-center gap-3 rounded-xl border border-border bg-secondary/30 p-4 text-sm text-muted-foreground">
              {signIn.starting || identity.isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Button size="sm" onClick={() => signIn.start()}>
                  <Github className="mr-1.5 h-3.5 w-3.5" />
                  {t("marketplace.identity_sign_in")}
                </Button>
              )}
              <span>{t("marketplace.identity_requesting")}</span>
            </div>
          )}

          {signIn.error && (
            <p className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {signIn.error}
            </p>
          )}

          <ul className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
            <li className="flex items-start gap-2 rounded-lg border border-border/70 bg-background/50 p-2.5">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              {t("marketplace.identity_fact_scope")}
            </li>
            <li className="flex items-start gap-2 rounded-lg border border-border/70 bg-background/50 p-2.5">
              <Store className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              {t("marketplace.identity_fact_name")}
            </li>
            <li className="flex items-start gap-2 rounded-lg border border-border/70 bg-background/50 p-2.5">
              <LogOut className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              {t("marketplace.identity_fact_keyring")}
            </li>
          </ul>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
          {signedIn ? (
            <Button size="sm" onClick={onClose}>
              {t("marketplace.identity_done")}
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t("marketplace.close")}
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

/**
 * The header chip: "Sign in with GitHub" while signed out, the avatar and
 * handle with a small menu while signed in. Hidden entirely when publishing
 * is disabled in this deployment — a door that answers 503 is not a door.
 */
export function PublisherChip({
  onMine,
  onSignIn,
}: {
  /** Jump to "my publications" — the list filtered to this account. */
  onMine?: () => void;
  /** Open the sign-in dialog; the chip only asks, the view owns the dialog. */
  onSignIn: () => void;
}) {
  const t = useT();
  const ready = useLocaleChunk("marketplace");
  const identity = usePublishIdentity();
  const signIn = useGithubSignIn();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const data = identity.data;
  if (data && !data.enabled && !data.wallpapers_enabled) return null;

  if (identity.isLoading || !ready) {
    return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />;
  }

  if (!data?.signed_in) {
    return (
      <Button
        size="sm"
        variant="outline"
        onClick={onSignIn}
        data-testid="publisher-chip-signed-out"
        title={data?.unreachable ? t("marketplace.identity_unreachable") : undefined}
      >
        <Github className="mr-1.5 h-3.5 w-3.5" />
        {t("marketplace.identity_sign_in")}
      </Button>
    );
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="publisher-chip"
        className={cn(
          "flex h-8 items-center gap-2 rounded-full border border-border bg-card/70 pl-1 pr-2.5",
          "text-xs font-medium text-foreground backdrop-blur transition-colors hover:border-primary/50",
        )}
      >
        <PublisherAvatar login={data.login} url={data.avatar_url} size={24} />
        <span className="max-w-[10rem] truncate">@{data.login}</span>
        <ChevronDown className="h-3 w-3 text-muted-foreground" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1.5 w-56 overflow-hidden rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-xl"
        >
          <div className="px-2.5 py-2">
            <p className="truncate text-xs font-semibold text-foreground">@{data.login}</p>
            <p className="text-[11px] text-muted-foreground">
              {t("marketplace.identity_menu_hint")}
            </p>
          </div>
          {onMine && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onMine();
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-secondary"
            >
              <Store className="h-3.5 w-3.5 text-muted-foreground" />
              {t("marketplace.identity_menu_mine")}
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              signIn.signOut();
            }}
            disabled={signIn.signingOut}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-foreground transition-colors hover:bg-secondary"
          >
            <LogOut className="h-3.5 w-3.5 text-muted-foreground" />
            {t("marketplace.identity_sign_out")}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * The compact identity card inside a publish form or dialog: who you are, or
 * the ticket to become somebody. `blurb` lets each surface say what
 * publishing means where it stands — the consequence of publishing a plugin
 * and of publishing a picture are not the same sentence.
 */
export function PublishIdentityCard({
  identity,
  loading,
  blurb,
  compact = false,
}: {
  identity: PublishIdentityWire | undefined;
  loading: boolean;
  blurb?: string;
  compact?: boolean;
}) {
  const t = useT();
  useLocaleChunk("marketplace");
  const signIn = useGithubSignIn();
  const signedIn = identity?.signed_in === true;
  return (
    <section
      className={cn(!compact && "rounded-xl border border-border bg-card/40 p-4")}
      data-testid="publish-identity"
      data-signed-in={signedIn ? "yes" : "no"}
    >
      <div className="flex flex-wrap items-center gap-3">
        {signedIn ? (
          <PublisherAvatar login={identity?.login} url={identity?.avatar_url} size={32} />
        ) : (
          <span className="grid h-8 w-8 place-items-center rounded-full bg-foreground text-background">
            <Github className="h-4 w-4" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-foreground">
            {t("marketplace.identity_label")}
          </p>
          <p className="text-xs text-muted-foreground">
            {signedIn ? (
              <>
                {fill(t("marketplace.identity_signed_in_as"), { login: identity?.login ?? "" })}
                {" — "}
                {blurb ?? t("marketplace.identity_publishes_under")}
              </>
            ) : (
              t("marketplace.identity_sign_in_blurb")
            )}
          </p>
          {identity?.unreachable && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("marketplace.identity_unreachable")}
            </p>
          )}
        </div>
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : signedIn ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => signIn.signOut()}
            disabled={signIn.signingOut}
          >
            <LogOut className="mr-1.5 h-3.5 w-3.5" />
            {t("marketplace.identity_sign_out")}
          </Button>
        ) : signIn.flow ? null : (
          <Button size="sm" onClick={() => signIn.start()} disabled={signIn.starting}>
            {signIn.starting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Github className="mr-1.5 h-3.5 w-3.5" />
            )}
            {t("marketplace.identity_sign_in")}
          </Button>
        )}
      </div>

      {signIn.flow && (
        <div className="mt-3">
          <DeviceCodeTicket flow={signIn.flow} onCancel={signIn.cancel} />
        </div>
      )}
      {signIn.error && (
        <p className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {signIn.error}
        </p>
      )}
    </section>
  );
}
