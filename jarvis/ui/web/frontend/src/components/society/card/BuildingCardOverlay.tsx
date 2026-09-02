/**
 * The building card: what opens when a building on the island is clicked,
 * the way an agent's card opens for a figure (maintainer, 2026-09-02).
 *
 * Two columns under one header. Left, the building itself, turned by hand,
 * in the island's own look (BuildingViewer — the same GLB and toon ramp the
 * map draws). Right, the explanation: what it does, how a person uses it,
 * and — for a hub — what stands inside it right now, loaded from the same
 * endpoints the hub drawer reads, so nothing is invented. The footer opens
 * the app section the building stands for; the Foundry's also creates an
 * agent.
 *
 * Same window as the agent card: inset over the world, the world still
 * visible through the scrim, Esc and ✕ close.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Plus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useLocaleChunk, useT } from "@/i18n";
import { useEventStore } from "@/store/events";

import { BuildingViewer } from "../figures/BuildingViewer";
import { HUBS } from "../world/HubDrawer";
import { BUILDING_CARDS, type BuildingPlace } from "./buildingCards";

export interface BuildingCardOverlayProps {
  place: BuildingPlace | null;
  onClose: () => void;
  /** The Foundry's card creates an agent through the section's creator. */
  onCreateAgent?: () => void;
}

export function BuildingCardOverlay({ place, onClose, onCreateAgent }: BuildingCardOverlayProps) {
  const t = useT();
  const ready = useLocaleChunk("society");
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const card = place ? BUILDING_CARDS[place] : null;
  const hub = card?.hub ? HUBS[card.hub] : null;
  const inside = useQuery({
    queryKey: ["society", "hub", card?.hub ?? ""],
    queryFn: hub?.load ?? (async () => []),
    enabled: Boolean(hub?.load) && place !== null,
    staleTime: 60_000,
  });
  const total = inside.data ? inside.data.reduce((n, g) => n + g.items.length, 0) : 0;
  const open = card !== null && ready;

  return (
    <Dialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="building-card"
          className="fixed inset-4 z-50 flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-float focus:outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none lg:inset-10"
        >
          {card ? (
            <>
              <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
                <div className="min-w-0 flex-1">
                  <Dialog.Title className="truncate font-display text-base font-semibold tracking-tight text-foreground">
                    {t(`society.world.${card.nameKey}`)}
                  </Dialog.Title>
                  <Dialog.Description className="truncate text-xs text-muted-foreground">
                    {t(`society.world.${card.taglineKey}`)}
                  </Dialog.Description>
                </div>
                <Badge variant="outline">{t("society.world.card_kind_building")}</Badge>
                <Dialog.Close
                  aria-label={t("society.world.drawer_close")}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
                >
                  <X className="h-4 w-4" aria-hidden />
                </Dialog.Close>
              </header>

              <div className="grid min-h-0 flex-1 grid-cols-[minmax(360px,1.35fr)_minmax(300px,1fr)]">
                {/* ---- left: the building, live ---- */}
                <section className="relative min-h-0 border-r border-border" aria-label={t(`society.world.${card.nameKey}`)}>
                  <BuildingViewer model={card.model} />
                </section>

                {/* ---- right: what it does, how to use it, what is inside ---- */}
                <section className="flex min-h-0 flex-col" aria-label={t("society.world.card_does_title")}>
                  <ScrollArea className="min-h-0 flex-1">
                    <div className="flex flex-col gap-5 p-5">
                      <div>
                        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          {t("society.world.card_does_title")}
                        </h3>
                        <p className="text-sm leading-relaxed text-foreground">{t(`society.world.${card.doesKey}`)}</p>
                      </div>
                      <div>
                        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          {t("society.world.card_how_title")}
                        </h3>
                        <p className="text-sm leading-relaxed text-foreground">{t(`society.world.${card.howKey}`)}</p>
                      </div>
                      {hub?.load ? (
                        <div data-testid="building-card-inside">
                          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            {t("society.world.card_inside_title")}
                          </h3>
                          {inside.isLoading ? (
                            <p className="text-sm text-muted-foreground">{t("society.world.drawer_loading")}</p>
                          ) : inside.isError ? (
                            <p className="text-sm text-destructive">{String(inside.error)}</p>
                          ) : total === 0 ? (
                            <p className="text-sm text-muted-foreground">{t("society.world.drawer_empty")}</p>
                          ) : (
                            inside.data
                              ?.filter((g) => g.items.length > 0)
                              .map((g) => (
                                <div key={g.labelKey} className="mb-3">
                                  <h4 className="mb-1.5 flex items-center gap-2 text-xs font-medium text-muted-foreground">
                                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: g.color }} aria-hidden />
                                    {t(`society.world.${g.labelKey}`)}
                                    <span className="tabular-nums">{g.items.length}</span>
                                  </h4>
                                  <ul className="flex flex-wrap gap-1.5">
                                    {g.items.map((n) => (
                                      <li key={n} className="rounded-md bg-secondary px-2 py-1 font-mono text-xs text-foreground">
                                        {n}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              ))
                          )}
                        </div>
                      ) : null}
                    </div>
                  </ScrollArea>
                  <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-5 py-3">
                    {card.builds && onCreateAgent ? (
                      <Button type="button" size="sm" onClick={onCreateAgent} data-testid="building-card-create">
                        <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                        {t("society.world.card_create_agent")}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => {
                        setActiveSection(card.section);
                        onClose();
                      }}
                    >
                      <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                      {t("society.world.card_open_section")}
                    </Button>
                  </footer>
                </section>
              </div>
            </>
          ) : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default BuildingCardOverlay;
