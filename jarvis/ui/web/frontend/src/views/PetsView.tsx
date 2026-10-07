import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useReducedMotion } from "framer-motion";
import {
  Check,
  Eye,
  EyeOff,
  Loader2,
  PawPrint,
  Plus,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { CreatePetDialog } from "@/components/pets/CreatePetDialog";
import { PetControlStripPreview } from "@/components/pets/PetControlStripPreview";
import { PetSprite, integerScaleFor } from "@/components/pets/PetSprite";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useKeybinds } from "@/hooks/useHotkey";
import { useOverlayStyle } from "@/hooks/useOverlayStyle";
import {
  useDeletePet,
  usePets,
  useSavePetSettings,
  useSelectPet,
  useSetPetVisible,
} from "@/hooks/usePets";
import { useRestartApp } from "@/hooks/useRestartApp";
import { useT } from "@/i18n";
import { NO_PET_ID, PET_STATES } from "@/lib/petStates";
import { previewPetScale, type Pet } from "@/lib/petsApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { KeybindRow, formatCombo } from "@/views/settings/KeybindRow";

/**
 * Settings → My Pets — the desktop pet (`docs/pets.md`).
 *
 * Top to bottom: what a pet is and the shortcut that hides it; a notice when
 * the desktop shows another display style, with the one click that switches
 * it; the active pet playing through all seven states over the strip it
 * carries, with its size, bubble and shortcut behind "Customize"; then every
 * pet as a tile — "None" first — plus "Create pet".
 *
 * Everything reads from one query (`usePets`), which the `PetChanged` event
 * refreshes, so the shortcut or another window moves this page too.
 */

/** How long the big preview holds each state before showing the next. */
const STATE_CYCLE_MS = 2500;
/** Largest edge of the big preview and of a grid tile's sprite, in px. */
const HERO_SPRITE_PX = 168;
const TILE_SPRITE_PX = 96;
const SCALE_MIN = 0.5;
const SCALE_MAX = 2;
/** Fine enough that the thumb glides; the pet itself grows in whole pixels. */
const SCALE_STEP = 0.01;

function useStateCycle(enabled: boolean): number {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (!enabled) {
      setIndex(0);
      return;
    }
    const timer = window.setInterval(
      () => setIndex((current) => (current + 1) % PET_STATES.length),
      STATE_CYCLE_MS,
    );
    return () => window.clearInterval(timer);
  }, [enabled]);
  return index;
}

export function PetsView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const pets = usePets();
  const overlay = useOverlayStyle();
  const keybinds = useKeybinds();
  const select = useSelectPet();
  const setVisible = useSetPetVisible();
  const remove = useDeletePet();
  const restartApp = useRestartApp();

  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Pet | null>(null);
  const [switchingStyle, setSwitchingStyle] = useState(false);
  const [needsRestart, setNeedsRestart] = useState(false);

  const data = pets.data;
  const activePet =
    data && data.active !== NO_PET_ID
      ? data.pets.find((pet) => pet.id === data.active) ?? null
      : null;
  const styleIsPet = overlay.config?.style === "pet";
  const shortcut = keybinds.config?.keybinds?.pet_toggle ?? "";

  const describe = (pet: Pet) => {
    const key = `pets.builtin.${pet.id}.description`;
    const localized = pet.builtin ? t(key) : key;
    return localized !== key ? localized : pet.description;
  };

  /**
   * The master switch: ON makes the pet the desktop overlay style, OFF hands
   * the desktop back to the default Jarvis bar. The chosen pet is kept either
   * way, so switching back on brings the same pet back.
   */
  async function setPetEnabled(enabled: boolean) {
    setSwitchingStyle(true);
    try {
      const result = await overlay.saveStyle(enabled ? "pet" : "jarvis_bar");
      if (result.applied_live) {
        pushToast("success", t(enabled ? "pets.style_saved" : "pets.style_off_saved"));
      } else {
        setNeedsRestart(true);
      }
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      setSwitchingStyle(false);
    }
  }

  function choose(petId: string) {
    if (!data || data.active === petId || select.isPending) return;
    select.mutate(petId, {
      onError: (error) =>
        pushToast("error", t("pets.save_error").replace("{0}", (error as Error).message)),
    });
  }

  function toggleVisible() {
    if (!data) return;
    setVisible.mutate(!data.visible, {
      onError: (error) =>
        pushToast("error", t("pets.save_error").replace("{0}", (error as Error).message)),
    });
  }

  function confirmDelete() {
    const pet = pendingDelete;
    if (!pet) return;
    remove.mutate(pet.id, {
      onSuccess: () => pushToast("success", t("pets.deleted").replace("{0}", pet.name)),
      onError: (error) =>
        pushToast("error", t("pets.save_error").replace("{0}", (error as Error).message)),
      onSettled: () => setPendingDelete(null),
    });
  }

  return (
    <div
      data-testid="pets-view"
      className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis"
    >
      <div className="w-full max-w-[1400px]">
        <PageHeader
          icon={<PawPrint />}
          title={t("pets.title")}
          description={
            shortcut
              ? t("pets.subtitle").replace("{0}", formatCombo(shortcut))
              : t("pets.subtitle_no_shortcut")
          }
          actions={
            <div className="flex items-center gap-3">
              {styleIsPet && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  data-testid="pets-visibility"
                  disabled={!data || setVisible.isPending}
                  onClick={toggleVisible}
                >
                  {data?.visible === false ? <Eye aria-hidden /> : <EyeOff aria-hidden />}
                  {data?.visible === false ? t("pets.show") : t("pets.hide")}
                </Button>
              )}
              <label className="flex items-center gap-2.5 rounded-lg border border-border bg-card px-3 py-1.5">
                <span className="text-sm font-medium text-foreground">{t("pets.enabled_label")}</span>
                <Switch
                  data-testid="pets-enabled"
                  checked={styleIsPet}
                  disabled={!overlay.config || switchingStyle}
                  aria-label={t("pets.enabled_label")}
                  onCheckedChange={(next) => void setPetEnabled(next)}
                />
              </label>
            </div>
          }
        />

        {overlay.config && !styleIsPet && !needsRestart && (
          <div
            data-testid="pets-style-notice"
            className="mb-5 flex flex-wrap items-center gap-4 rounded-xl border border-border bg-card p-5"
          >
            <PawPrint className="h-6 w-6 shrink-0 text-accent" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-base font-medium text-foreground">
                {t("pets.style_notice_title")}
              </p>
              <p className="mt-0.5 text-sm text-muted-foreground">
                {t("pets.style_notice_body").replace(
                  "{0}",
                  t(`settings_view.overlay_style.options.${overlay.config.style}`),
                )}
              </p>
            </div>
            <Button
              type="button"
              data-testid="pets-use-pet"
              disabled={switchingStyle}
              onClick={() => void setPetEnabled(true)}
            >
              {switchingStyle && <Loader2 className="animate-spin" aria-hidden />}
              {t("pets.use_pet")}
            </Button>
          </div>
        )}

        {needsRestart && (
          <div
            data-testid="pets-restart-notice"
            className="mb-5 flex flex-wrap items-center gap-4 rounded-xl border border-border bg-card p-5"
          >
            <p className="min-w-0 flex-1 text-base text-foreground">{t("pets.restart_hint")}</p>
            <Button
              type="button"
              data-testid="pets-restart"
              disabled={restartApp.restarting}
              onClick={() => void restartApp.restart()}
            >
              {restartApp.restarting && <Loader2 className="animate-spin" aria-hidden />}
              {restartApp.buttonLabel}
            </Button>
          </div>
        )}

        {pets.isError ? (
          <div className="rounded-xl border border-border bg-card p-5" role="alert">
            <p className="text-base text-destructive">
              {t("pets.load_error").replace("{0}", (pets.error as Error).message)}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => void pets.refetch()}
            >
              {t("common.retry")}
            </Button>
          </div>
        ) : (
          <>
            <ActivePetCard
              pet={activePet}
              loading={!data}
              description={activePet ? describe(activePet) : t("pets.none_description")}
              customizeOpen={customizeOpen}
              onToggleCustomize={() => setCustomizeOpen((open) => !open)}
            >
              {customizeOpen && data && (
                <CustomizePanel
                  scale={data.scale}
                  bubble={data.bubble}
                  stripAlways={data.strip_always}
                  keybinds={keybinds}
                />
              )}
            </ActivePetCard>

            <div className="mb-3 mt-8 flex items-center justify-between gap-4">
              <h2 className="text-lg font-semibold text-foreground-strong">{t("pets.my_pets")}</h2>
              <Button
                type="button"
                variant="outline"
                size="sm"
                data-testid="pets-create"
                disabled={!data}
                onClick={() => setCreateOpen(true)}
              >
                <Plus aria-hidden />
                {t("pets.create")}
              </Button>
            </div>

            <ul
              data-testid="pets-grid"
              className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(200px,1fr))]"
            >
              {!data ? (
                [0, 1, 2, 3].map((index) => (
                  <li
                    key={index}
                    aria-hidden
                    className="h-[196px] animate-pulse rounded-xl border border-border bg-foreground/5"
                  />
                ))
              ) : (
                <>
                  <li>
                    <PetTile
                      id={NO_PET_ID}
                      name={t("pets.none_name")}
                      description={t("pets.none_description")}
                      selected={data.active === NO_PET_ID}
                      disabled={select.isPending}
                      onSelect={() => choose(NO_PET_ID)}
                      selectedLabel={t("pets.selected")}
                    >
                      <PetControlStripPreview size="sm" companion />
                    </PetTile>
                  </li>
                  {data.pets.map((pet) => (
                    <li key={pet.id}>
                      <PetTile
                        id={pet.id}
                        name={pet.name}
                        description={describe(pet)}
                        selected={data.active === pet.id}
                        disabled={select.isPending}
                        onSelect={() => choose(pet.id)}
                        selectedLabel={t("pets.selected")}
                        onDelete={pet.builtin ? undefined : () => setPendingDelete(pet)}
                        deleteLabel={t("pets.delete_named").replace("{0}", pet.name)}
                      >
                        <PetSprite
                          pet={pet}
                          state="idle"
                          scale={integerScaleFor(pet.frame_size, TILE_SPRITE_PX)}
                        />
                      </PetTile>
                    </li>
                  ))}
                </>
              )}
            </ul>
          </>
        )}
      </div>

      <CreatePetDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(pet) => {
          pushToast("success", t("pets.created").replace("{0}", pet.name));
          choose(pet.id);
        }}
      />

      <DeletePetDialog
        pet={pendingDelete}
        busy={remove.isPending}
        onCancel={() => setPendingDelete(null)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}

function ActivePetCard({
  pet,
  loading,
  description,
  customizeOpen,
  onToggleCustomize,
  children,
}: {
  pet: Pet | null;
  loading: boolean;
  description: string;
  customizeOpen: boolean;
  onToggleCustomize: () => void;
  children?: React.ReactNode;
}) {
  const t = useT();
  const reduced = useReducedMotion() ?? false;
  const stateIndex = useStateCycle(Boolean(pet) && !reduced);
  const state = PET_STATES[stateIndex];
  const stateLabel = t(`pets.states.${state}`);

  return (
    <section
      data-testid="pets-active-card"
      aria-label={t("pets.preview_title")}
      className="grid gap-5 rounded-xl border border-border bg-card p-5 md:grid-cols-[minmax(0,300px)_minmax(0,1fr)]"
    >
      <div className="flex flex-col items-center gap-3 rounded-lg bg-secondary/60 px-4 py-5">
        <div className="grid h-[180px] w-full place-items-center">
          {loading ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
          ) : pet ? (
            <PetSprite
              pet={pet}
              state={state}
              scale={integerScaleFor(pet.frame_size, HERO_SPRITE_PX)}
              label={`${pet.name}: ${stateLabel}`}
            />
          ) : (
            <p className="text-sm text-muted-foreground">{t("pets.none_description")}</p>
          )}
        </div>
        {pet && (
          <span
            data-testid="pets-state-label"
            className="rounded-full border border-border bg-background px-2.5 py-0.5 text-xs font-medium text-muted-foreground"
          >
            {stateLabel}
          </span>
        )}
        <PetControlStripPreview companion />
      </div>

      <div className="flex min-w-0 flex-col">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm text-muted-foreground">{t("pets.preview_title")}</p>
            <p className="mt-0.5 truncate text-lg font-semibold text-foreground-strong">
              {loading ? " " : pet ? pet.name : t("pets.none_name")}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">{loading ? "" : description}</p>
          </div>
          <Button
            type="button"
            variant={customizeOpen ? "secondary" : "outline"}
            size="sm"
            data-testid="pets-customize"
            aria-expanded={customizeOpen}
            disabled={loading}
            onClick={onToggleCustomize}
          >
            <SlidersHorizontal aria-hidden />
            {t("pets.customize")}
          </Button>
        </div>
        {children}
      </div>
    </section>
  );
}

function CustomizePanel({
  scale,
  bubble,
  stripAlways,
  keybinds,
}: {
  scale: number;
  bubble: boolean;
  stripAlways: boolean;
  keybinds: ReturnType<typeof useKeybinds>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const save = useSavePetSettings();
  const [size, setSize] = useState(scale);
  // While the thumb moves the pet follows it live: one preview request at a
  // time, always the newest value, nothing written. The release saves.
  const dragging = useRef(false);
  const slider = useRef<HTMLInputElement>(null);
  const preview = useRef<{ inFlight: Promise<void> | null; next: number | null; sent: boolean }>({
    inFlight: null,
    next: null,
    sent: false,
  });

  // Follow the server (another window, a failed save), but never yank the
  // thumb out from under a drag.
  useEffect(() => {
    if (!dragging.current) setSize(scale);
  }, [scale]);

  function previewSize(next: number) {
    const queue = preview.current;
    queue.next = Math.round(next * 100) / 100;
    if (queue.inFlight) return;
    const pump = async () => {
      while (queue.next !== null) {
        const value = queue.next;
        queue.next = null;
        queue.sent = true;
        try {
          await previewPetScale(value);
        } catch {
          // A missed drag step is harmless: the release saves the final size
          // and reports any error then.
        }
      }
    };
    queue.inFlight = pump().finally(() => {
      queue.inFlight = null;
    });
  }

  // The release can happen anywhere on screen, not only over the track.
  function startDrag() {
    window.addEventListener(
      "pointerup",
      () => {
        if (slider.current) void commitSize(Number(slider.current.value));
      },
      { once: true },
    );
  }

  function changeSize(next: number) {
    dragging.current = true;
    setSize(next);
    previewSize(next);
  }

  async function commitSize(next: number) {
    dragging.current = false;
    const rounded = Math.round(next * 100) / 100;
    const queue = preview.current;
    queue.next = null;
    const previewed = queue.sent;
    queue.sent = false;
    // The save must land after the last preview, or that preview would win.
    if (queue.inFlight) await queue.inFlight;
    if (rounded === scale && !previewed) return;
    save.mutate(
      { scale: rounded },
      {
        onError: (error) => {
          setSize(scale);
          pushToast("error", t("pets.save_error").replace("{0}", (error as Error).message));
        },
      },
    );
  }

  return (
    <div
      data-testid="pets-customize-panel"
      className="mt-4 divide-y divide-border rounded-lg border border-border bg-background"
    >
      <div className="px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          <label htmlFor="pets-size" className="text-sm font-medium text-foreground">
            {t("pets.size_label")}
          </label>
          <span className="font-mono text-sm text-foreground-strong">{`${Math.round(size * 100)}%`}</span>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">{t("pets.size_hint")}</p>
        <input
          id="pets-size"
          ref={slider}
          type="range"
          data-testid="pets-size"
          min={SCALE_MIN}
          max={SCALE_MAX}
          step={SCALE_STEP}
          value={size}
          onChange={(event) => changeSize(Number(event.target.value))}
          onPointerDown={startDrag}
          onKeyUp={(event) => void commitSize(Number(event.currentTarget.value))}
          onBlur={(event) => {
            if (dragging.current) void commitSize(Number(event.currentTarget.value));
          }}
          className="mt-3 w-full accent-primary disabled:opacity-50"
        />
      </div>
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">{t("pets.bubble_label")}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("pets.bubble_hint")}</p>
        </div>
        <Switch
          checked={bubble}
          disabled={save.isPending}
          aria-label={t("pets.bubble_label")}
          data-testid="pets-bubble"
          onCheckedChange={(next) =>
            save.mutate(
              { bubble: next },
              {
                onError: (error) =>
                  pushToast("error", t("pets.save_error").replace("{0}", (error as Error).message)),
              },
            )
          }
        />
      </div>
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">{t("pets.strip_label")}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("pets.strip_hint")}</p>
        </div>
        <Switch
          checked={stripAlways}
          disabled={save.isPending}
          aria-label={t("pets.strip_label")}
          data-testid="pets-strip-always"
          onCheckedChange={(next) =>
            save.mutate(
              { strip_always: next },
              {
                onError: (error) =>
                  pushToast("error", t("pets.save_error").replace("{0}", (error as Error).message)),
              },
            )
          }
        />
      </div>
      <div className="px-4 py-3">
        <KeybindRow
          action="pet_toggle"
          label={t("settings_view.keybinds.pet_toggle_label")}
          config={keybinds.config}
          loading={keybinds.loading}
          onSave={keybinds.saveKeybind}
        />
        <p className="mt-1.5 text-xs text-muted-foreground">{t("pets.shortcut_hint")}</p>
      </div>
    </div>
  );
}

function PetTile({
  id,
  name,
  description,
  selected,
  disabled,
  selectedLabel,
  onSelect,
  onDelete,
  deleteLabel,
  children,
}: {
  id: string;
  name: string;
  description: string;
  selected: boolean;
  disabled: boolean;
  selectedLabel: string;
  onSelect: () => void;
  onDelete?: () => void;
  deleteLabel?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="group relative h-full">
      <button
        type="button"
        data-testid={`pet-tile-${id}`}
        aria-pressed={selected}
        disabled={disabled && !selected}
        onClick={onSelect}
        className={cn(
          "flex h-full w-full flex-col rounded-xl border bg-card p-3 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          selected
            ? "border-accent ring-1 ring-accent"
            : "border-border hover:border-border-strong",
        )}
      >
        <div className="grid h-28 w-full place-items-center overflow-hidden rounded-lg bg-secondary/60">
          {children}
        </div>
        <div className="mt-3 flex min-w-0 items-center gap-2 pr-6">
          <span className="truncate text-base font-medium text-foreground">{name}</span>
          {selected && (
            <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-accent">
              <Check className="h-3.5 w-3.5" aria-hidden />
              {selectedLabel}
            </span>
          )}
        </div>
        <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">{description}</p>
      </button>
      {onDelete && (
        <button
          type="button"
          data-testid={`pet-delete-${id}`}
          aria-label={deleteLabel}
          title={deleteLabel}
          onClick={onDelete}
          className="absolute bottom-3 right-3 grid h-7 w-7 place-items-center rounded-md text-muted-foreground opacity-70 transition-colors hover:bg-secondary hover:text-destructive hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Trash2 className="h-4 w-4" aria-hidden />
        </button>
      )}
    </div>
  );
}

function DeletePetDialog({
  pet,
  busy,
  onCancel,
  onConfirm,
}: {
  pet: Pet | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  return (
    <Dialog.Root open={pet !== null} onOpenChange={(open) => !open && !busy && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-scrim/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="delete-pet-dialog"
          className="fixed inset-0 z-[80] m-auto h-fit w-[min(400px,calc(100vw-32px))] rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-float outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none"
        >
          <Dialog.Title className="text-base font-semibold text-foreground-strong">
            {t("pets.delete_title").replace("{0}", pet?.name ?? "")}
          </Dialog.Title>
          <Dialog.Description className="mt-1.5 text-sm text-muted-foreground">
            {t("pets.delete_body")}
          </Dialog.Description>
          <div className="mt-6 flex justify-end gap-2">
            <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
              {t("common.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive"
              data-testid="delete-pet-confirm"
              disabled={busy}
              onClick={onConfirm}
            >
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              {t("pets.delete")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
