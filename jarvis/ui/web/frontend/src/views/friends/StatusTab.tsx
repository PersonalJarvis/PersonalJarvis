// === F-FRIENDS [F4] · feature/friends-section · alex-2026-05-01 ===
import { Loader2, Radio, ShieldAlert } from "lucide-react";
import { useEventStore } from "@/store/events";

import { PermissionMatrix } from "@/components/friends/PermissionMatrix";
import {
  type FriendItem,
  type StatusProfile,
  useFriendPermission,
  useFriends,
  useUpdatePermission,
} from "@/hooks/useFriends";

/**
 * Status-Sharing-Konfiguration pro Friend.
 *
 * Phase F4 (live): pro Friend wird das aktive Sharing-Profile angezeigt
 * und ueber 3-Radio-Selektion ge-PATCHt. Live-Status-Cards (vergangene
 * Updates pro Friend) kommen mit dem WebSocket-Stream in F5.
 *
 * Hard-Blacklist-Hinweis am Ende: rohe Utterances, Tool-Args, Stacktraces,
 * Memory-Updates verlassen die Maschine NIE — egal welches Profile aktiv ist.
 */
export function StatusTab() {
  const assistantName = useEventStore((s) => s.assistantName);
  const friends = useFriends();

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card/30 p-5">
        <div className="flex items-center gap-2 font-display text-sm font-semibold text-foreground">
          <Radio className="h-4 w-4 text-primary" />
          Status-Sharing
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Du entscheidest pro Friend, wie viel von deinem {assistantName}-Status
          ausgespielt wird. Privacy-First: Default ist <strong>minimal</strong>{" "}
          (nur online/offline), Aenderungen wirken sofort.
        </p>
      </div>

      <ProfileLegend />

      {friends.isLoading && (
        <div className="flex items-center justify-center rounded-md border border-border bg-card/20 py-6 text-sm text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Friends laden ...
        </div>
      )}

      {friends.isError && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Friends konnten nicht geladen werden:{" "}
          {(friends.error as Error).message}
        </div>
      )}

      {friends.data && friends.data.length === 0 && (
        <div className="rounded-md border border-dashed border-border/60 px-3 py-4 text-center text-xs text-muted-foreground">
          Noch keine Friends. Wechsle auf den Chat-Tab und klicke{" "}
          <strong>+ Friend hinzufuegen</strong>.
        </div>
      )}

      {friends.data && friends.data.length > 0 && (
        <div className="space-y-3">
          {friends.data.map((f) => (
            <FriendPermissionRow key={f.id} friend={f} />
          ))}
        </div>
      )}

      <div className="rounded-md border border-amber-400/40 bg-amber-400/10 px-4 py-3 text-[11px] text-amber-100">
        <div className="flex items-center gap-2 font-display text-xs font-semibold text-amber-300">
          <ShieldAlert className="h-3.5 w-3.5" />
          Hard-Blacklist (immer blockiert)
        </div>
        <p className="mt-1 text-amber-100/90">
          Rohe Utterances, Tool-Args, Stacktraces, Memory-Updates,
          Window-Titles. Diese verlassen deine Maschine NIE - egal welches
          Profile aktiv ist und egal was eine Custom-Whitelist sagt.
        </p>
      </div>
    </div>
  );
}

function FriendPermissionRow({ friend }: { friend: FriendItem }) {
  const permission = useFriendPermission(friend.id);
  const update = useUpdatePermission();

  const handleChange = (profile: StatusProfile) => {
    update.mutate({ friend_id: friend.id, profile });
  };

  const initial = friend.display_name.slice(0, 1).toUpperCase();
  const current: StatusProfile = permission.data?.profile ?? "minimal";

  return (
    <div className="rounded-xl border border-border bg-card/30 p-4">
      <div className="flex items-start gap-3">
        <div
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-border bg-muted/30 text-sm font-medium text-muted-foreground"
          aria-hidden
        >
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between">
            <span className="truncate text-sm font-medium text-foreground">
              {friend.display_name}
            </span>
            {update.isPending && (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> speichern ...
              </span>
            )}
            {update.isError && (
              <span className="text-[10px] text-destructive">
                Fehler: {(update.error as Error).message}
              </span>
            )}
          </div>
          {friend.note && (
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
              {friend.note}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3">
        {permission.isLoading ? (
          <div className="flex items-center text-[11px] text-muted-foreground">
            <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            Permission laden ...
          </div>
        ) : (
          <PermissionMatrix
            friendId={friend.id}
            current={current}
            onChange={handleChange}
            disabled={update.isPending}
          />
        )}
      </div>
    </div>
  );
}

function ProfileLegend() {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <ProfileCard
        name="minimal"
        subline="Nur online/offline"
        description="Friend sieht: Voice-Session aktiv ja/nein, Dauer. Keine Inhalte."
        isDefault
      />
      <ProfileCard
        name="standard"
        subline="+ Mission-Titel"
        description="Friend sieht zusaetzlich: Welche Missions/Tasks gerade laufen, Erfolg, Dauer."
      />
      <ProfileCard
        name="detailed"
        subline="+ OpenClaw-Summary"
        description="Friend sieht zusaetzlich: Signierte OpenClaw-Summaries (KEINE Utterances)."
      />
    </div>
  );
}

function ProfileCard({
  name,
  subline,
  description,
  isDefault,
}: {
  name: string;
  subline: string;
  description: string;
  isDefault?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex items-center justify-between">
        <span className="font-display text-sm font-semibold uppercase tracking-wider text-foreground">
          {name}
        </span>
        {isDefault && (
          <span className="rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-primary">
            default
          </span>
        )}
      </div>
      <div className="mt-1 text-[11px] uppercase tracking-wider text-muted-foreground">
        {subline}
      </div>
      <p className="mt-2 text-xs text-foreground/80">{description}</p>
    </div>
  );
}
