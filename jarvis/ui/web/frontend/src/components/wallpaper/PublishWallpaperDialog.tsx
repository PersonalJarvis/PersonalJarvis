import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Check, Loader2, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  InstallStandard,
  type InstallStandardWire,
} from "@/components/InstallStandard";
import {
  PublishIdentityCard,
  usePublishIdentity,
} from "@/components/marketplace/PublishIdentity";
import { fullUrlFor, type WallpaperEntry } from "@/hooks/useWallpaperCatalog";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// "Share to community" — publishing one of your own wallpapers.
//
// What this dialog owes the person using it is honesty about three things,
// because all three are irreversible in ways a picker normally is not:
//
//   1. It goes PUBLIC, under their GitHub name, and nobody reviews it first.
//   2. The license they pick is the permission strangers get — this lane
//      only accepts licenses that allow redistribution, because the picture
//      is copied onto other people's machines.
//   3. Taking it down later removes it from the store, not from the machines
//      that already imported it.
//
// The rights checkbox is the record that carries the lane: no human inspects
// the image before it is live, so the uploader's own statement is the thing
// that makes publishing an accountable act rather than an anonymous one.
// ---------------------------------------------------------------------------

/** Exactly the licenses the endpoint accepts (publish.py WALLPAPER_LICENSES). */
const LICENSES = [
  {
    id: "CC0-1.0",
    label: "CC0 — public domain",
    hint: "Anyone may use it for anything, no credit required.",
  },
  {
    id: "CC-BY-4.0",
    label: "CC BY — credit required",
    hint: "Anyone may use it, as long as they credit you.",
  },
  {
    id: "CC-BY-SA-4.0",
    label: "CC BY-SA — credit, share alike",
    hint: "As CC BY, and anything built on it carries the same license.",
  },
] as const;

const MAX_TITLE_CHARS = 80;
const MAX_DESCRIPTION_CHARS = 500;

interface PublishedWire {
  ok: boolean;
  name: string;
  version: string;
  install?: InstallStandardWire | null;
}

interface FieldErrorWire {
  field: string | null;
  error: string;
}

export function PublishWallpaperDialog({
  item,
  onClose,
}: {
  item: WallpaperEntry;
  onClose: () => void;
}) {
  const identity = usePublishIdentity();
  const signedIn = identity.data?.signed_in === true;

  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState("");
  const [license, setLicense] = useState<string>(LICENSES[0].id);
  const [rights, setRights] = useState(false);
  const [error, setError] = useState<FieldErrorWire | null>(null);
  const [published, setPublished] = useState<PublishedWire | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const publish = useMutation({
    mutationFn: async (): Promise<PublishedWire> => {
      const res = await fetch("/api/marketplace/publish/submit-wallpaper", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          upload_id: item.id,
          title: title.trim(),
          description: description.trim(),
          license,
          // The picker's own light/dark filing travels with the picture, so
          // it lands in the right half of somebody else's store too.
          theme: item.theme,
          rights,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = (body as { detail?: unknown }).detail;
        if (detail && typeof detail === "object") {
          const shaped = detail as FieldErrorWire;
          throw Object.assign(new Error(shaped.error), { field: shaped.field ?? null });
        }
        throw new Error(
          typeof detail === "string" ? detail : `Publishing failed (${res.status})`,
        );
      }
      return body as PublishedWire;
    },
    onSuccess: (result) => {
      setError(null);
      setPublished(result);
    },
    onError: (err: Error & { field?: string | null }) => {
      setError({ field: err.field ?? null, error: err.message });
    },
  });

  const titleTooLong = title.trim().length > MAX_TITLE_CHARS;
  const descriptionTooLong = description.trim().length > MAX_DESCRIPTION_CHARS;
  const ready = signedIn && rights && title.trim().length > 0 && !titleTooLong && !descriptionTooLong;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Share ${item.title} with the community`}
      data-testid="publish-wallpaper-dialog"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-background/80 p-6 backdrop-blur-sm"
    >
      <div className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
        <header className="flex items-center gap-3 border-b border-border px-5 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold">Share with the community</h2>
            <p className="truncate text-xs text-muted-foreground">
              {published
                ? "Published — it appears in the store once the feed rebuilds."
                : "Anyone using Personal Jarvis will be able to add this wallpaper."}
            </p>
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          <div className="flex gap-4">
            <img
              src={fullUrlFor(item)}
              alt={item.title}
              className="h-24 w-40 shrink-0 rounded-lg border border-border object-cover"
            />
            <p className="text-xs leading-relaxed text-muted-foreground">
              This publishes the picture publicly, under your GitHub name, with no
              review before it goes live. Only upload something you made or hold the
              rights to. If you take it down later it disappears from the store — not
              from machines that already added it.
            </p>
          </div>

          {published ? (
            <PublishedPanel result={published} />
          ) : (
            <>
              <PublishIdentityCard
                identity={identity.data}
                loading={identity.isLoading}
                blurb="this wallpaper publishes under this name."
              />

              <Field label="Title" error={error?.field === "title" ? error.error : null}>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={MAX_TITLE_CHARS * 2}
                  className={inputCls(error?.field === "title" || titleTooLong)}
                  placeholder="Rain Antenna City"
                />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {titleTooLong
                    ? `${title.trim().length}/${MAX_TITLE_CHARS} — too long.`
                    : "The store turns this into the wallpaper's address, so make it distinctive."}
                </p>
              </Field>

              <Field
                label="Description (optional)"
                error={error?.field === "description" ? error.error : null}
              >
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  className={cn(
                    inputCls(error?.field === "description" || descriptionTooLong),
                    "resize-y",
                  )}
                  placeholder="Neon rooftops and antenna forests in the rain."
                />
              </Field>

              <Field label="License" error={error?.field === "license" ? error.error : null}>
                <div className="space-y-1.5">
                  {LICENSES.map((entry) => (
                    <label
                      key={entry.id}
                      className={cn(
                        "flex cursor-pointer items-start gap-2.5 rounded-lg border p-2.5 transition-colors",
                        license === entry.id
                          ? "border-primary bg-primary/5"
                          : "border-border hover:bg-muted/40",
                      )}
                    >
                      <input
                        type="radio"
                        name="wallpaper-license"
                        checked={license === entry.id}
                        onChange={() => setLicense(entry.id)}
                        className="mt-0.5 accent-primary"
                      />
                      <span className="min-w-0">
                        <span className="block text-xs font-medium text-foreground">
                          {entry.label}
                        </span>
                        <span className="block text-[11px] text-muted-foreground">
                          {entry.hint}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  Only licenses that allow redistribution — the picture is copied onto
                  other people's machines.
                </p>
              </Field>

              <label
                className={cn(
                  "flex cursor-pointer items-start gap-2.5 rounded-lg border p-3 transition-colors",
                  error?.field === "rights"
                    ? "border-destructive/50 bg-destructive/5"
                    : "border-border hover:bg-muted/40",
                )}
              >
                <input
                  type="checkbox"
                  checked={rights}
                  onChange={(e) => setRights(e.target.checked)}
                  className="mt-0.5 accent-primary"
                  data-testid="wallpaper-rights"
                />
                <span className="text-xs leading-relaxed text-foreground">
                  I made this picture or hold the rights to publish it, and it is legal
                  to share — no copyrighted work of someone else, nothing hateful, and
                  nothing that depicts a real person without their consent.
                </span>
              </label>

              {error && error.field === null && (
                <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {error.error}
                </p>
              )}
            </>
          )}
        </div>

        <footer className="flex items-center gap-2 border-t border-border px-5 py-3">
          <p className="min-w-0 flex-1 text-[11px] text-muted-foreground">
            {published
              ? "You can close this — nothing else is needed."
              : signedIn
                ? "Nothing is uploaded until you click Publish."
                : "Sign in with GitHub above to publish."}
          </p>
          {published ? (
            <Button size="sm" onClick={onClose}>
              Done
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => publish.mutate()}
              disabled={!ready || publish.isPending}
              data-testid="wallpaper-publish-submit"
            >
              {publish.isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="mr-1.5 h-3.5 w-3.5" />
              )}
              Publish
            </Button>
          )}
        </footer>
      </div>
    </div>
  );
}

/** After the upload: what it is called now, and how anyone else gets it. */
function PublishedPanel({ result }: { result: PublishedWire }) {
  return (
    <div className="space-y-3">
      <p className="flex items-start gap-2 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs text-foreground">
        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
        <span>
          Published as <span className="font-mono font-medium">{result.name}</span>. The
          store rebuilds its list every few minutes — it appears there, and in this app's
          Community section, shortly.
        </span>
      </p>
      {result.install && <InstallStandard install={result.install} />}
    </div>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error: string | null;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-foreground">{label}</label>
      {children}
      {error && (
        <p className="mt-1 flex items-start gap-1.5 text-[11px] text-destructive">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

function inputCls(hasError: boolean): string {
  return cn(
    "w-full rounded-md border bg-background px-2.5 py-1.5 text-xs text-foreground",
    "placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary",
    hasError ? "border-destructive/60" : "border-border",
  );
}
