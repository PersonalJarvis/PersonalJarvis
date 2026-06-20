import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { useFriendsList, useUpdateFriend, type FriendItem } from "@/hooks/useFederation";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

const INTERVAL_PRESETS_S = [60, 120, 300, 900];

/**
 * Liste aller Friends + Per-Friend-Pull-Interval-Stepper.
 *
 * Plan §D-Spec: Per-Friend-Sync-Interval-Setting. Der Backend-Constraint
 * ist 60..3600 s; wir bieten vier sinnvolle Presets statt freiem Input.
 */
export function FriendsList() {
  const t = useT();
  const friends = useFriendsList();
  const update = useUpdateFriend();
  const [pendingPubkey, setPendingPubkey] = useState<string | null>(null);

  if (friends.isLoading) {
    return <div className="h-12 animate-pulse rounded-md bg-muted/10" />;
  }
  if (!friends.data) return null;

  if (friends.data.friends.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border/60 p-3 text-xs text-muted-foreground">
        {t("friends_list.empty")}
      </div>
    );
  }

  return (
    <ul className="space-y-2">
      {friends.data.friends.map((f) => (
        <FriendRow
          key={f.pubkey}
          item={f}
          isPending={pendingPubkey === f.pubkey && update.isPending}
          onUpdateInterval={(s) => {
            setPendingPubkey(f.pubkey);
            update.mutate({ pubkey: f.pubkey, pull_interval_s: s });
          }}
        />
      ))}
    </ul>
  );
}

function FriendRow({
  item, isPending, onUpdateInterval,
}: {
  item: FriendItem;
  isPending: boolean;
  onUpdateInterval: (s: number) => void;
}) {
  const t = useT();
  return (
    <li className="rounded-md border border-border bg-background/40 p-3 text-xs">
      <div className="flex items-start gap-3">
        <Sparkles className="mt-0.5 h-3.5 w-3.5 text-primary" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="font-medium">{item.display_name}</div>
              <code className="font-mono text-[10px] text-muted-foreground">
                {item.pubkey.slice(0, 16)}…
              </code>
            </div>
            <div className="text-right text-[10px] text-muted-foreground">
              <div>{`${t("friends_list.since")} ${new Date(item.paired_at).toLocaleDateString("de-DE")}`}</div>
              {item.last_pull_at && (
                <div>{`${t("friends_list.last_pull")} ${timeSince(item.last_pull_at)}`}</div>
              )}
            </div>
          </div>

          <div className="mt-2 flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t("friends_list.pull_every")}
            </span>
            <div className="flex gap-1">
              {INTERVAL_PRESETS_S.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={isPending}
                  onClick={() => onUpdateInterval(s)}
                  className={cn(
                    "rounded-md border px-2 py-0.5 text-[10px] transition-colors",
                    item.pull_interval_s === s
                      ? "border-primary/40 bg-primary/10 text-primary"
                      : "border-border/60 bg-background/40 text-muted-foreground hover:bg-background/60",
                    isPending && "opacity-50 cursor-wait",
                  )}
                >
                  {formatInterval(s)}
                </button>
              ))}
            </div>
            {isPending && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
          </div>
        </div>
      </div>
    </li>
  );
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function timeSince(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 1) return "gerade";
  if (minutes < 60) return `vor ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `vor ${hours}h`;
  return `vor ${Math.floor(hours / 24)}d`;
}
