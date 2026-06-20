import { useState } from "react";
import { Loader2, MessageSquarePlus, RefreshCw, Sparkles } from "lucide-react";
import {
  useFeed, useFriendsList, useSendReaction,
  type ActivityItemDTO, type Reaction,
} from "@/hooks/useFederation";
import { ReactionBar } from "@/components/board/ReactionBar";
import { StoryComposer } from "@/components/board/StoryComposer";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

export function ActivityFeed() {
  const t = useT();
  const [sort, setSort] = useState<"interesting" | "latest">("interesting");
  const [composerOpen, setComposerOpen] = useState(false);
  const feed = useFeed(sort);
  const friends = useFriendsList();
  const sendReact = useSendReaction();

  const friendsByPubkey = new Map(
    (friends.data?.friends ?? []).map((f) => [f.pubkey, f]),
  );

  return (
    <section className="space-y-4 rounded-xl border border-border bg-card/30 p-5 backdrop-blur">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="font-display text-sm font-semibold flex-1">{t("activity_feed.title")}</h3>
        <SortToggle value={sort} onChange={setSort} />
        <button
          type="button"
          onClick={() => feed.refetch()}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-[11px] hover:border-primary/40"
          title={t("activity_feed.refresh_tooltip")}
        >
          <RefreshCw className={cn("h-3 w-3", feed.isFetching && "animate-spin")} />
          Refresh
        </button>
        <button
          type="button"
          onClick={() => setComposerOpen(true)}
          className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1 text-[11px] text-primary hover:bg-primary/20"
        >
          <MessageSquarePlus className="h-3 w-3" />
          Story
        </button>
      </header>

      {feed.isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> {t("activity_feed.loading")}
        </div>
      )}
      {feed.isError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
          {(feed.error as Error).message}
        </div>
      )}

      {feed.data?.items.length === 0 && (
        <div className="rounded-md border border-dashed border-border/60 p-4 text-xs text-muted-foreground">
          {t("activity_feed.empty")}
        </div>
      )}

      <ul className="space-y-3">
        {(feed.data?.items ?? []).map((it) => (
          <FeedRow
            key={it.id} item={it}
            friendName={friendsByPubkey.get(it.author_pubkey)?.display_name}
            onReact={(r) => sendReact.mutate({
              item_id: it.id, reaction: r, author_pubkey: it.author_pubkey,
            })}
          />
        ))}
      </ul>

      {composerOpen && <StoryComposer onClose={() => setComposerOpen(false)} />}
    </section>
  );
}

function SortToggle({
  value, onChange,
}: { value: "interesting" | "latest"; onChange: (v: "interesting" | "latest") => void }) {
  return (
    <div className="flex gap-0.5 rounded-md border border-border bg-background/40 p-0.5">
      <button
        onClick={() => onChange("interesting")}
        className={cn(
          "rounded px-2 py-0.5 text-[10px] uppercase tracking-wider transition-colors",
          value === "interesting" ? "bg-primary/15 text-primary" : "text-muted-foreground",
        )}
      >Interesting</button>
      <button
        onClick={() => onChange("latest")}
        className={cn(
          "rounded px-2 py-0.5 text-[10px] uppercase tracking-wider transition-colors",
          value === "latest" ? "bg-primary/15 text-primary" : "text-muted-foreground",
        )}
      >Latest</button>
    </div>
  );
}

function FeedRow({
  item, friendName, onReact,
}: {
  item: ActivityItemDTO;
  friendName: string | undefined;
  onReact: (r: Reaction) => void;
}) {
  const author = friendName ?? item.author_display_name ?? `${item.author_pubkey.slice(0, 8)}…`;
  return (
    <li className="rounded-lg border border-border bg-background/40 p-3">
      <header className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <Sparkles className="h-3 w-3 text-primary" />
        <span>{author}</span>
        <span>·</span>
        <span>{new Date(item.created_at).toLocaleString("de-DE")}</span>
        <span>·</span>
        <span className={cn(
          item.visibility === "public" && "text-emerald-300",
          item.visibility === "private" && "text-amber-300",
        )}>{item.visibility}</span>
      </header>
      <ItemBody item={item} />
      <footer className="mt-2 flex items-center justify-between">
        <ReactionBar
          counts={item.reaction_counts}
          hasReactions={item.has_reactions}
          onReact={onReact}
        />
      </footer>
    </li>
  );
}

function ItemBody({ item }: { item: ActivityItemDTO }) {
  if (item.kind === "story") {
    const text = (item.payload?.text as string) ?? "";
    return <p className="whitespace-pre-wrap text-sm">{text}</p>;
  }
  if (item.kind === "achievement_unlocked") {
    const id = (item.payload?.achievement_id as string) ?? "achievement";
    return (
      <p className="text-sm">
        <span className="font-medium">Achievement: </span>
        <span className="font-mono text-primary">{id}</span>
      </p>
    );
  }
  return (
    <p className="text-xs text-muted-foreground">{item.kind}</p>
  );
}
