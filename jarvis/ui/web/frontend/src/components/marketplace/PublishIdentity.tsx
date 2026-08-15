import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Copy, ExternalLink, Loader2, LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import { robustCopy } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";

// ---------------------------------------------------------------------------
// One identity for everything the app publishes.
//
// Packages and wallpapers are two lanes with two endpoints, but a person signs
// in once — so the sign-in lives here rather than inside either surface. Both
// read the same query key, which means signing in from the wallpaper picker
// leaves the Publish tab signed in and the other way round, with no state to
// keep in step.
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
 * The sign-in panel: state, the device code, and sign-out.
 *
 * `blurb` lets each surface say what publishing means where it stands — the
 * consequence of publishing a plugin and of publishing a picture are not the
 * same sentence, and a shared component that flattened them would make the
 * wallpaper lane sound more reviewed than it is.
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
  const queryClient = useQueryClient();
  const [flow, setFlow] = useState<SigninStartWire | null>(null);
  const [flowError, setFlowError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const startMutation = useMutation({
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
      setFlowError(null);
    },
    onError: (error: Error) => setFlowError(error.message),
  });

  const signOutMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/marketplace/publish/identity", { method: "DELETE" });
      if (!res.ok) throw new Error(`Sign-out failed (${res.status})`);
      return res.json();
    },
    onSuccess: () => {
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
          queryClient.invalidateQueries({ queryKey: PUBLISH_IDENTITY_KEY });
        } else if (data.status === "error") {
          setFlow(null);
          setFlowError(data.error ?? "sign-in failed");
        }
      } catch {
        // Network blip — keep polling until the flow expires.
      }
    }, intervalMs);
    return () => clearInterval(timer);
  }, [flow, queryClient]);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  const signedIn = identity?.signed_in === true;
  return (
    <section
      className={cnCard(compact)}
      data-testid="publish-identity"
      data-signed-in={signedIn ? "yes" : "no"}
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-foreground">
            Identity
          </p>
          <p className="text-xs text-muted-foreground">
            {signedIn ? (
              <>
                Signed in as{" "}
                <span className="font-medium text-foreground">@{identity?.login}</span>
                {blurb ? ` — ${blurb}` : " — your packages publish under this name."}
              </>
            ) : (
              "Sign in with GitHub to publish. The sign-in only proves who you are — it grants nothing on your account."
            )}
          </p>
          {identity?.unreachable && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              GitHub is unreachable right now — your sign-in state could not be checked.
            </p>
          )}
        </div>
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : signedIn ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => signOutMutation.mutate()}
            disabled={signOutMutation.isPending}
          >
            <LogOut className="mr-1.5 h-3.5 w-3.5" />
            Sign out
          </Button>
        ) : flow ? null : (
          <Button size="sm" onClick={() => startMutation.mutate()} disabled={startMutation.isPending}>
            {startMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Sign in with GitHub
          </Button>
        )}
      </div>

      {flow && (
        <div className="mt-3 rounded-md border border-border bg-muted/30 p-3">
          <p className="text-xs text-muted-foreground">
            Enter this code at{" "}
            <button
              type="button"
              onClick={() =>
                openExternalUrl(flow.verification_uri ?? "https://github.com/login/device")
              }
              className="font-medium text-primary hover:underline"
            >
              github.com/login/device
              <ExternalLink className="ml-0.5 inline h-3 w-3" />
            </button>{" "}
            — waiting for your approval…
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="rounded-md border border-border bg-background px-3 py-1.5 font-mono text-lg font-semibold tracking-[0.25em] text-foreground">
              {flow.user_code}
            </code>
            <button
              type="button"
              onClick={async () => {
                if (await robustCopy(flow.user_code)) setCopied(true);
              }}
              className="grid h-7 w-7 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title={copied ? "Copied" : "Copy code"}
              aria-label="Copy the sign-in code"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-primary" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </button>
            <Loader2 className="ml-auto h-3.5 w-3.5 animate-spin text-muted-foreground" />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                // Tell the backend to drop the flow, then forget it locally
                // either way — an already-finished flow 404s harmlessly.
                void fetch(`/api/marketplace/publish/signin/${flow.flow_id}`, {
                  method: "DELETE",
                }).catch(() => undefined);
                setFlow(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {flowError && (
        <p className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {flowError}
        </p>
      )}
    </section>
  );
}

/** Bordered on a page, plain inside a dialog that already has a frame. */
function cnCard(compact: boolean): string {
  return compact ? "" : "rounded-lg border border-border bg-card/40 p-4";
}
