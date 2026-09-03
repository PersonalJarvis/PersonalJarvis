/**
 * The identity card — who Jarvis thinks you are, in one calm object.
 *
 * The portrait doubles as the upload control (there is no second "change
 * picture" button anywhere), the headline is how the app addresses you, and
 * the progress bar is the only place completeness is drawn. When nothing has
 * been learned yet the card does not go quiet: it names the two ways to teach
 * it, because an empty profile is a first-run state, not a failure.
 */
import { useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Camera, FileText, Loader2, Mic, Trash2, UserCircle2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { IdentityAvatar } from "@/components/identity/IdentityAvatar";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import type { ProfileResponse } from "@/views/profile/api";
import {
  TOTAL_FIELDS,
  acquaintanceStage,
  countFilled,
  displayAddress,
} from "@/views/profile/ledger";

export function IdentityCard({
  data,
  meta,
  onOpenSource,
}: {
  data: ProfileResponse;
  meta: Record<string, unknown>;
  /** Switches the view to the source-file tab — the second way to teach it. */
  onOpenSource: () => void;
}) {
  const t = useT();
  const name = data.user.name?.trim() || null;

  const filled = useMemo(() => countFilled(meta), [meta]);
  const stage = acquaintanceStage(filled, TOTAL_FIELDS);
  const percent = Math.round((filled / Math.max(TOTAL_FIELDS, 1)) * 100);

  // Address the user the way they asked to be addressed ("Chef"), falling back
  // to their first name — warmer than the full legal name.
  const address = displayAddress(meta, name);
  const headline = address
    ? t(`profile_view.stage_headline.${stage.key}`).replace("{0}", `, ${address}`)
    : t("profile_view.hero_name_placeholder");

  const ratio = t("profile_view.entries_ratio")
    .replace("{0}", String(filled))
    .replace("{1}", String(TOTAL_FIELDS));

  const peopleLine =
    data.people.length === 1
      ? t("profile_view.person_known").replace("{0}", "1")
      : t("profile_view.people_known").replace("{0}", String(data.people.length));

  return (
    <Card className="profile-rise p-5">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-4">
        <AvatarButton name={name} hasAvatar={!!data.has_avatar} />

        <div className="min-w-0 flex-1 basis-64">
          <h2 className="truncate text-xl font-semibold text-foreground-strong">{headline}</h2>
          <p className="mt-1 text-base text-muted-foreground">
            {t(`profile_view.stages.${stage.key}`)}
          </p>
        </div>

        <div className="flex min-w-0 flex-1 basis-64 flex-col gap-2 sm:max-w-xs">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-base tabular-nums text-foreground">{ratio}</span>
            <span className="text-sm tabular-nums text-muted-foreground">{peopleLine}</span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={TOTAL_FIELDS}
            aria-valuenow={filled}
            aria-label={ratio}
            data-testid="profile-progress"
            className="h-1.5 w-full overflow-hidden rounded-full bg-secondary"
          >
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      </div>

      {filled === 0 && (
        <div className="mt-5 border-t border-border pt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-foreground-faint">
            {t("profile_view.hero_teach_intro")}
          </p>
          <div className="mt-3 flex flex-col gap-3 md:flex-row md:gap-8">
            <p className="flex min-w-0 flex-1 items-start gap-2 text-base text-foreground-secondary">
              <Mic aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <span>{t("profile_view.hero_teach_voice")}</span>
            </p>
            <p className="flex min-w-0 flex-1 items-start gap-2 text-base text-foreground-secondary">
              <FileText aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <span>
                {t("profile_view.hero_teach_file")}{" "}
                <Button variant="link" className="align-baseline" onClick={onOpenSource}>
                  {t("profile_view.hero_open_source")}
                </Button>
              </span>
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}

// ----------------------------------------------------------------------
// AvatarButton — the portrait, and the whole upload control
// ----------------------------------------------------------------------
//
// The avatar bytes live under user_data_dir()/data and are served by
// GET /api/profile/avatar. A hidden <input type="file"> is .click()'d to open
// the OS picker; a cache-busting query param forces the <img> to reload after
// a replace or a delete. The portrait IS the control — click to pick a file,
// and a small remove badge appears on hover or keyboard focus once there is a
// picture to remove.

function AvatarButton({ name, hasAvatar }: { name: string | null; hasAvatar: boolean }) {
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
        title={hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
        aria-label={hasAvatar ? t("profile_view.avatar_change") : t("profile_view.avatar_upload")}
        className="relative flex h-14 w-14 items-center justify-center overflow-hidden rounded-full bg-secondary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
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
