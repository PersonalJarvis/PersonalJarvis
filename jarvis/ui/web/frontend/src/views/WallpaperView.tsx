import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Image as ImageIcon,
  Loader2,
  Moon,
  RotateCcw,
  Sun,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ViewHeader } from "@/views/ChatsView";
import {
  fullUrlFor,
  thumbUrlFor,
  useWallpaperCatalog,
  type WallpaperEntry,
} from "@/hooks/useWallpaperCatalog";
import { useApplyWallpaper } from "@/hooks/useApplyWallpaper";
import { useThemeValue } from "@/hooks/useTheme";
import { useWallpaperStore } from "@/store/wallpaper";
import { cn } from "@/lib/utils";

type ThemeFilter = "all" | "light" | "dark";
type Ordering = "mixed" | "style";

/**
 * A stable pseudo-random rank for a wallpaper id (FNV-1a).
 *
 * The default order is shuffled on purpose: the library is stored twenty-five
 * wallpapers to a style, and listing it in storage order means scrolling past
 * twenty-five variations of the same look before seeing a different one. What
 * it must not be is *re*-shuffled — an order that changed on every render, or
 * on every visit, would move a tile the moment someone reached for it. Hashing
 * the id gives an order that looks random and never moves.
 */
function shuffleRank(id: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash;
}

/** The segmented filter/ordering controls share one look. */
function SegmentButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition-colors",
        active
          ? "bg-primary/15 text-primary"
          : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * One grid tile.
 *
 * The image is the 480 px thumbnail and it is lazy: five hundred tiles must
 * cost only what is actually scrolled past. The full-size file is not touched
 * here at all — that download belongs to the preview.
 */
function WallpaperTile({
  item,
  active,
  onOpen,
}: {
  item: WallpaperEntry;
  active: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`${item.title} — ${item.styleLabel}`}
      className={cn(
        "group relative overflow-hidden rounded-lg border text-left transition-all",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
        active
          ? "border-primary ring-2 ring-primary/60"
          : "border-border hover:border-primary/50",
      )}
    >
      <img
        src={thumbUrlFor(item)}
        alt={item.title}
        // The pinned original is the first thing on screen and the shell has
        // already loaded it, so waiting for an intersection buys nothing.
        loading={item.isDefault ? "eager" : "lazy"}
        decoding="async"
        width={480}
        height={270}
        className="aspect-video w-full bg-secondary/40 object-cover transition-transform duration-300 group-hover:scale-[1.03]"
      />
      <span className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/75 to-transparent px-2.5 pb-1.5 pt-6">
        <span className="block truncate text-[11px] font-medium text-white">
          {item.title}
        </span>
        <span className="block truncate text-[10px] text-white/65">
          {item.styleLabel}
        </span>
      </span>
      {active && (
        <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground shadow">
          <Check className="h-3.5 w-3.5" />
        </span>
      )}
    </button>
  );
}

/**
 * The full-size preview.
 *
 * This is the only place the real 1920x1080 file is requested, which is the
 * whole point of the two-tier delivery: browsing costs thumbnails, and a
 * wallpaper is downloaded when someone actually wants to look at it. The
 * spinner stays until the image reports itself loaded so the frame never shows
 * a half-painted picture.
 */
function WallpaperPreview({
  item,
  applied,
  onApply,
  onClose,
  onStep,
}: {
  item: WallpaperEntry;
  applied: boolean;
  onApply: () => void;
  onClose: () => void;
  onStep: (delta: number) => void;
}) {
  const [loaded, setLoaded] = useState(false);

  // The preview is keyed on the wallpaper, so a step to the next one remounts
  // this state — but an explicit reset keeps that guarantee local.
  useEffect(() => setLoaded(false), [item.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowLeft") onStep(-1);
      else if (event.key === "ArrowRight") onStep(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onStep]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${item.title} preview`}
      data-testid="wallpaper-preview"
      className="fixed inset-0 z-50 flex flex-col bg-background/85 backdrop-blur-xl"
    >
      <header className="flex items-center gap-3 border-b border-border px-6 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold">{item.title}</h3>
          <p className="truncate text-xs text-muted-foreground">
            {item.styleLabel} · {item.theme === "dark" ? "Dark" : "Light"}
          </p>
        </div>
        <Button
          size="sm"
          variant={applied ? "secondary" : "default"}
          onClick={onApply}
          disabled={applied}
        >
          {applied ? (
            <>
              <Check className="mr-1.5 h-3.5 w-3.5" /> In use
            </>
          ) : (
            "Use this wallpaper"
          )}
        </Button>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close preview">
          <X className="h-4 w-4" />
        </Button>
      </header>

      <div className="relative flex flex-1 items-center justify-center overflow-hidden p-6">
        {!loaded && (
          <Loader2 className="absolute h-6 w-6 animate-spin text-muted-foreground" />
        )}
        <img
          key={item.id}
          src={fullUrlFor(item)}
          alt={item.title}
          onLoad={() => setLoaded(true)}
          onError={() => setLoaded(true)}
          className={cn(
            "max-h-full max-w-full rounded-lg object-contain shadow-2xl transition-opacity duration-200",
            loaded ? "opacity-100" : "opacity-0",
          )}
        />
        <button
          type="button"
          onClick={() => onStep(-1)}
          aria-label="Previous wallpaper"
          className="absolute left-3 flex h-10 w-10 items-center justify-center rounded-full border border-border bg-background/70 text-foreground transition-colors hover:bg-background"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
        <button
          type="button"
          onClick={() => onStep(1)}
          aria-label="Next wallpaper"
          className="absolute right-3 flex h-10 w-10 items-center justify-center rounded-full border border-border bg-background/70 text-foreground transition-colors hover:bg-background"
        >
          <ChevronRight className="h-5 w-5" />
        </button>
      </div>
    </div>
  );
}

/**
 * The Wallpaper section: browse the generated library, preview one at full
 * size, and adopt it as the app's ground.
 *
 * Adopting one also switches light/dark to match the artwork — see
 * `useApplyWallpaper`. The bundled default is always one click away again, so
 * trying a wallpaper is meant to feel as reversible as it actually is.
 */
export function WallpaperView() {
  const { data, isLoading } = useWallpaperCatalog();
  // Each theme keeps its own pick; the grid marks the one worn by the mode
  // the app is in right now (adopting a tile switches mode WITH the picture,
  // so the check mark always lands on what is actually on screen).
  const activeTheme = useThemeValue();
  const selectedId = useWallpaperStore((state) => state.selections[activeTheme]);
  const apply = useApplyWallpaper();

  const [theme, setTheme] = useState<ThemeFilter>("all");
  const [ordering, setOrdering] = useState<Ordering>("mixed");
  const [style, setStyle] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);

  const items = data?.items ?? [];
  const styles = data?.styles ?? [];

  const visible = useMemo(() => {
    const filtered = items.filter(
      (item) =>
        (theme === "all" || item.theme === theme) &&
        (style === null || item.style === style),
    );
    return filtered.sort((left, right) => {
      // The bundled original outranks every ordering and every shuffle: it is
      // the first wallpaper this app ever had, and it stays the first tile.
      if (left.isDefault !== right.isDefault) return left.isDefault ? -1 : 1;
      if (ordering === "style") {
        const byStyle = left.style.localeCompare(right.style);
        if (byStyle !== 0) return byStyle;
        return left.id.localeCompare(right.id);
      }
      return shuffleRank(left.id) - shuffleRank(right.id);
    });
  }, [items, ordering, style, theme]);

  // "No choice stored" and "the original is chosen" are the same state, so the
  // pinned tile carries the check mark on a fresh profile.
  const isApplied = (item: WallpaperEntry) =>
    item.isDefault ? selectedId === null : item.id === selectedId;

  const previewIndex = previewId
    ? visible.findIndex((item) => item.id === previewId)
    : -1;
  const previewItem = previewIndex >= 0 ? visible[previewIndex] : null;

  // Stepping wraps around, so the arrow keys never dead-end at either edge of
  // whatever the current filter happens to be showing.
  const step = useCallback(
    (delta: number) => {
      if (previewIndex < 0 || visible.length === 0) return;
      const next = (previewIndex + delta + visible.length) % visible.length;
      setPreviewId(visible[next].id);
    },
    [previewIndex, visible],
  );

  const header = (
    <ViewHeader
      icon={<ImageIcon className="h-4 w-4 text-primary" />}
      title="Wallpaper"
      subtitle={
        data
          ? `${data.count} wallpapers · ${styles.length} styles · picking one sets light or dark to match`
          : "The app's desktop background"
      }
      right={
        selectedId ? (
          <Button size="sm" variant="outline" onClick={() => apply(null)}>
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Default
          </Button>
        ) : undefined
      }
    />
  );

  if (isLoading) {
    return (
      <div className="flex h-full flex-col">
        {header}
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {header}

      {!data?.libraryAvailable && (
        <p className="border-b border-border px-6 py-2.5 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">
            No wallpaper library installed.
          </span>{" "}
          The generated collection lives in{" "}
          <code className="rounded bg-secondary/60 px-1 py-0.5">
            data/jarvis-wallpaper-gallery
          </code>
          , which is not part of the repository. Until it is there, the original
          below is the whole choice.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-6 py-3">
        <div className="flex items-center gap-1 rounded-lg border border-border p-0.5">
          <SegmentButton active={theme === "all"} onClick={() => setTheme("all")}>
            All
          </SegmentButton>
          <SegmentButton active={theme === "dark"} onClick={() => setTheme("dark")}>
            <Moon className="h-3 w-3" /> Dark
          </SegmentButton>
          <SegmentButton active={theme === "light"} onClick={() => setTheme("light")}>
            <Sun className="h-3 w-3" /> Light
          </SegmentButton>
        </div>

        <div className="flex items-center gap-1 rounded-lg border border-border p-0.5">
          <SegmentButton
            active={ordering === "mixed"}
            onClick={() => setOrdering("mixed")}
          >
            Mixed
          </SegmentButton>
          <SegmentButton
            active={ordering === "style"}
            onClick={() => setOrdering("style")}
          >
            By style
          </SegmentButton>
        </div>

        <span className="ml-auto text-xs text-muted-foreground">
          {visible.length} shown
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 border-b border-border px-6 py-2.5">
        <SegmentButton active={style === null} onClick={() => setStyle(null)}>
          All styles
        </SegmentButton>
        {styles.map((entry) => (
          <SegmentButton
            key={entry.slug}
            active={style === entry.slug}
            onClick={() => setStyle(entry.slug)}
          >
            {entry.label}
          </SegmentButton>
        ))}
      </div>

      <ScrollArea className="flex-1">
        <div
          data-testid="wallpaper-grid"
          className="grid gap-3 p-6 [grid-template-columns:repeat(auto-fill,minmax(210px,1fr))]"
        >
          {visible.map((item) => (
            <WallpaperTile
              key={item.id}
              item={item}
              active={isApplied(item)}
              onOpen={() => setPreviewId(item.id)}
            />
          ))}
        </div>
        {visible.length === 0 && (
          <p className="px-6 pb-6 text-xs text-muted-foreground">
            No wallpaper matches this filter.
          </p>
        )}
      </ScrollArea>

      {previewItem && (
        <WallpaperPreview
          item={previewItem}
          applied={isApplied(previewItem)}
          onApply={() => apply(previewItem)}
          onClose={() => setPreviewId(null)}
          onStep={step}
        />
      )}
    </div>
  );
}
