/**
 * The portrait, and the whole upload control.
 *
 * The avatar bytes live under user_data_dir()/data and are served by
 * GET /api/profile/avatar. A hidden <input type="file"> is .click()'d to open
 * the OS picker; a cache-busting query param forces the <img> to reload after
 * a replace or a delete. The portrait IS the control — click to pick a file,
 * and a small remove badge appears on hover or keyboard focus once there is a
 * picture to remove. There is no second "change picture" button anywhere.
 *
 * Lifted out of the old IdentityCard unchanged: the cache-busting and the
 * same-file-again reset are both load-bearing and were correct.
 */
import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Camera, Loader2, Trash2, UserCircle2 } from "lucide-react";

import { IdentityAvatar } from "@/components/identity/IdentityAvatar";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

export function AvatarButton({
  name,
  hasAvatar,
  size = "md",
}: {
  name: string | null;
  hasAvatar: boolean;
  /** `lg` is the dossier header; `md` keeps the old 56 px for anywhere else. */
  size?: "md" | "lg";
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [bust, setBust] = useState(0);

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/profile/avatar", { method: "POST", body: fd });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      return res.json();
    },
    onSuccess: () => {
      setBust(Date.now());
      pushToast("success", t("profile_view.avatar_uploaded"));
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const remove = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/profile/avatar", { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      setBust(Date.now());
      pushToast("info", t("profile_view.avatar_removed"));
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (err: Error) => pushToast("error", err.message),
  });

  const onFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset so picking the *same* file again still fires onChange.
    e.target.value = "";
    if (file) upload.mutate(file);
  };

  const busy = upload.isPending || remove.isPending;
  const box = size === "lg" ? "h-16 w-16" : "h-14 w-14";
  const title = hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload");

  return (
    <div className="group/avatar relative shrink-0">
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
        onChange={onFileChosen}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        title={title}
        aria-label={title}
        className={cn(
          "relative flex items-center justify-center overflow-hidden rounded-full bg-secondary transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50",
          box,
        )}
      >
        {/* The person's own coloured mark, not a grey disc: this is the app's
            own owner, and identity is one of the jobs colour is allowed. */}
        {name ? (
          <IdentityAvatar
            name={name}
            size="lg"
            src={hasAvatar ? `/api/profile/avatar?t=${bust}` : null}
            alt={t("profile_view.avatar_alt")}
          />
        ) : hasAvatar ? (
          <img
            src={`/api/profile/avatar?t=${bust}`}
            alt={t("profile_view.avatar_alt")}
            className="h-full w-full object-cover"
            draggable={false}
          />
        ) : (
          <UserCircle2 aria-hidden className="h-6 w-6 text-muted-foreground" />
        )}

        <span className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-full bg-scrim/70 opacity-0 transition-opacity duration-200 group-hover/avatar:opacity-100 group-focus-visible/avatar:opacity-100">
          <Camera aria-hidden className="h-4 w-4 text-foreground-strong" />
        </span>

        {busy && (
          <span className="absolute inset-0 flex items-center justify-center rounded-full bg-scrim/70">
            <Loader2 aria-hidden className="h-4 w-4 animate-spin text-foreground-strong" />
          </span>
        )}
      </button>

      {hasAvatar && (
        <button
          type="button"
          onClick={() => remove.mutate()}
          disabled={busy}
          title={t("profile_view.avatar_remove")}
          aria-label={t("profile_view.avatar_remove")}
          className={cn(
            "absolute -bottom-1 -right-1 rounded-full border border-border bg-card p-1 text-muted-foreground opacity-0 transition-opacity",
            "hover:text-destructive focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover/avatar:opacity-100",
          )}
        >
          <Trash2 aria-hidden className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}
