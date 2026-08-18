/**
 * Modes — pick the character the assistant answers in, or build a new one.
 *
 * Three parts, in the order the decision actually gets made: the shelf you
 * choose from, the open card that shows EVERYTHING a mode does, and the
 * workshop where you make (or change) one. Switching applies from the next turn
 * in voice AND in chat, with no restart, because both brains read the persona
 * through one function on the backend.
 *
 * Reading and choosing are two different clicks, on purpose. Clicking a card
 * opens it below the shelf — the full character text, the knobs, the voice —
 * without touching what the assistant is running, so you can read Coach without
 * becoming Coach. Every card and the open panel carry an explicit "Use" so the
 * switch is still one click and never a hidden gesture; the first version hid
 * "restore" behind a hover and nobody found it.
 *
 * Two facts, shown apart: what is IN FORCE and what the user CHOSE. They differ
 * while the Agentic IDE's coding mode overrides the persona, and a screen that
 * only knows the first cannot even confirm that a click did anything.
 *
 * The workshop offers two ways in, because describing a personality out loud
 * and typing one are genuinely different tasks. "Talk it through" opens a real
 * voice conversation: the assistant interviews you and writes the mode itself
 * with its `save_mode` tool. The written path hands the same job to the chat.
 * The form is also the editor: "Edit" on any card loads it there, and saving
 * writes it back under the same id (a built-in keeps its "restore original").
 *
 * Colours come from theme tokens only, so the view is correct in light and dark
 * without a second palette to keep in step.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Loader2,
  Mic,
  MicOff,
  Pencil,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useEventStore } from "@/store/events";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import { sendChatMessage } from "@/lib/chat";
import { cn } from "@/lib/utils";
import {
  activateMode,
  deleteMode,
  fetchModes,
  restoreBuiltin,
  saveMode,
  type AssistantMode,
  type ModeDraft,
  type ModesState,
  type Proactivity,
  type Verbosity,
} from "@/lib/modesApi";

/**
 * What the assistant is told when the user asks for an interview.
 *
 * Deliberately an instruction to ASK rather than to produce: handed "build me a
 * friendly mode", a model writes one immediately and the user gets whatever it
 * guessed. The point of the interview is that the questions surface the things
 * people do not think to say — whether they want to be disagreed with, what the
 * assistant should never do, how it should open a conversation.
 */
const INTERVIEW_BRIEF =
  "I want to create a new assistant mode — a character for you to answer in. " +
  "Interview me first: ask me one question at a time about how you should " +
  "behave. How should you greet me? Should you have opinions and disagree with " +
  "me? How long should your answers be? Should you ask about my day, or stay " +
  "on the task? What should you never do? When you have enough, read the mode " +
  "back to me in a sentence and save it with the save_mode tool.";

/** i18n keys for the two knobs — layer 4 of the five-layer enum pattern. */
const VERBOSITY_KEYS: Record<Verbosity, string> = {
  brief: "modes_view.verbosity_brief",
  normal: "modes_view.verbosity_normal",
  rich: "modes_view.verbosity_rich",
};

const PROACTIVITY_KEYS: Record<Proactivity, string> = {
  reactive: "modes_view.proactivity_reactive",
  normal: "modes_view.proactivity_normal",
  forward: "modes_view.proactivity_forward",
};

interface Draft extends ModeDraft {
  name: string;
  emoji: string;
  description: string;
  character: string;
  verbosity: Verbosity;
  proactivity: Proactivity;
}

const EMPTY_DRAFT: Draft = {
  name: "",
  emoji: "",
  description: "",
  character: "",
  verbosity: "normal",
  proactivity: "normal",
};

function fill(template: string, ...values: string[]): string {
  return values.reduce((text, value, i) => text.replace(`{${i}}`, value), template);
}

export function ModesView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const { active: voiceLive, busy: voiceBusy, connecting, toggleCall } = useVoiceCall();

  const [state, setState] = useState<ModesState | null>(null);
  const [loading, setLoading] = useState(true);
  const [switching, setSwitching] = useState("");
  const [viewing, setViewing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [written, setWritten] = useState("");
  const formRef = useRef<HTMLElement>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await fetchModes());
    } catch {
      // Backend still warming, or headless. Keep whatever we had: blanking the
      // shelf on a hiccup would claim the user has no modes, which is a claim
      // about their setup rather than about this one failed read.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /*
   * While a voice interview is running, watch for the mode it creates.
   *
   * The assistant saves through its own tool, not through this screen, so there
   * is no response here to react to. Polling is confined to exactly that window
   * — a call in progress — rather than running for the whole session, which is
   * how a background refresh quietly becomes a per-second request forever.
   */
  useEffect(() => {
    if (!voiceLive) return;
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [voiceLive, refresh]);

  const modes = useMemo(() => state?.modes ?? [], [state]);
  const active = state?.active ?? "";
  const chosen = state?.chosen ?? active;
  const override = state?.section_override ?? "";
  const nameOf = (slug: string) => modes.find((m) => m.slug === slug)?.name ?? slug;

  // The open card: what the user clicked, else the mode in force. Never a mode
  // that has since been deleted — that panel would describe a ghost.
  const shown = useMemo(() => {
    const wanted = viewing && modes.some((m) => m.slug === viewing) ? viewing : active;
    return modes.find((m) => m.slug === wanted) ?? null;
  }, [modes, viewing, active]);

  const choose = async (mode: AssistantMode) => {
    setSwitching(mode.slug);
    try {
      const next = await activateMode(mode.slug);
      setState(next);
      if (next.section_override && next.active !== mode.slug) {
        // The click DID something — it is stored — but nothing on the shelf
        // moves. Say so, or the user learns that the screen is broken.
        pushToast("info", fill(t("modes_view.switched_waiting"), mode.name, nameOf(next.active)));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSwitching("");
    }
  };

  const remove = async (mode: AssistantMode) => {
    try {
      setState(mode.built_in ? await restoreBuiltin(mode.slug) : await deleteMode(mode.slug));
      pushToast(
        "success",
        fill(t(mode.built_in ? "modes_view.restored" : "modes_view.deleted"), mode.name),
      );
      if (draft.slug === mode.slug) setDraft(EMPTY_DRAFT);
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  const startEditing = (mode: AssistantMode) => {
    setDraft({
      slug: mode.slug,
      name: mode.name,
      emoji: mode.emoji,
      description: mode.description,
      character: mode.character,
      voice: mode.voice,
      verbosity: mode.verbosity,
      proactivity: mode.proactivity,
    });
    // The form is below the fold on most screens; an "Edit" that visibly does
    // nothing reads as broken.
    formRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  const editing = draft.slug ? modes.find((m) => m.slug === draft.slug) ?? null : null;

  const create = async () => {
    if (!draft.name.trim() || !draft.character.trim()) {
      pushToast("warning", t("modes_view.needs_name"));
      return;
    }
    setSaving(true);
    try {
      setState(await saveMode(draft));
      pushToast("success", fill(t("modes_view.saved"), draft.name));
      if (draft.slug) setViewing(draft.slug);
      setDraft(EMPTY_DRAFT);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const startInterview = async () => {
    // Ending a call needs no brief — only the opening of one does.
    if (!voiceLive) pushToast("info", t("modes_view.interview_toast"));
    await toggleCall();
  };

  const describeInWriting = async () => {
    const text = written.trim();
    if (!text) return;
    const sent = await sendChatMessage(`${INTERVIEW_BRIEF}\n\nTo start: ${text}`);
    if (!sent) {
      pushToast("error", t("modes_view.chat_not_up"));
      return;
    }
    setWritten("");
    // The conversation happens in Chats, so go where the answer will appear
    // rather than leaving the user watching a screen that will not change.
    setActiveSection("chats");
  };

  const fieldClass =
    "rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary";

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <ViewHeader
        icon={<Sparkles className="h-4 w-4 text-muted-foreground" />}
        title={t("modes_view.title")}
        subtitle={t("modes_view.subtitle")}
      />

      {loading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="flex flex-col gap-8 px-6 py-6">
          {override && (
            <p
              data-testid="mode-override-banner"
              className="rounded-lg border border-border bg-secondary/40 px-4 py-3 text-sm text-muted-foreground"
            >
              {fill(t("modes_view.override_banner"), nameOf(override), nameOf(chosen))}
            </p>
          )}

          <section className="flex flex-col gap-3">
            <div>
              <h3 className="font-display text-sm font-semibold tracking-tight">
                {t("modes_view.shelf_title")}
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">{t("modes_view.shelf_hint")}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {modes.map((mode) => {
                const isActive = mode.slug === active;
                const isChosen = mode.slug === chosen && !isActive;
                const isShown = shown?.slug === mode.slug;
                return (
                  <button
                    key={mode.slug}
                    type="button"
                    onClick={() => setViewing(mode.slug)}
                    aria-pressed={isShown}
                    data-testid={`mode-card-${mode.slug}`}
                    data-active={isActive || undefined}
                    className={cn(
                      "group relative flex flex-col gap-2 rounded-xl border p-4 text-left transition-colors",
                      isActive
                        ? "border-primary bg-primary/10"
                        : isChosen
                          ? "border-dashed border-primary/60 bg-secondary/30 hover:bg-secondary/60"
                          : "border-border bg-secondary/30 hover:bg-secondary/60",
                      isShown && "ring-2 ring-primary/50",
                    )}
                  >
                    <div className="flex items-center gap-2 pr-6">
                      <span className="text-lg leading-none">{mode.emoji || "•"}</span>
                      <span className="font-medium">{mode.name}</span>
                    </div>
                    <p className="text-sm text-muted-foreground">{mode.description}</p>
                    <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                      <span>{t(VERBOSITY_KEYS[mode.verbosity])}</span>
                      <span aria-hidden>·</span>
                      <span>{t(PROACTIVITY_KEYS[mode.proactivity])}</span>
                      <span className="ml-auto">
                        {isActive ? (
                          <span className="flex items-center gap-1 font-medium text-primary">
                            <Check className="h-3.5 w-3.5" />
                            {t("modes_view.in_use")}
                          </span>
                        ) : switching === mode.slug ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <span
                            role="button"
                            tabIndex={0}
                            data-testid={`mode-use-${mode.slug}`}
                            aria-label={fill(t("modes_view.use_named"), mode.name)}
                            onClick={(e) => {
                              e.stopPropagation();
                              if (!switching) void choose(mode);
                            }}
                            onKeyDown={(e) => {
                              if (e.key !== "Enter" && e.key !== " ") return;
                              e.stopPropagation();
                              e.preventDefault();
                              if (!switching) void choose(mode);
                            }}
                            className={cn(
                              "rounded-md border px-2 py-0.5 font-medium transition-colors",
                              isChosen
                                ? "border-primary/60 text-primary"
                                : "border-border text-foreground hover:border-primary hover:bg-primary hover:text-primary-foreground",
                            )}
                          >
                            {isChosen ? t("modes_view.your_choice") : t("modes_view.use")}
                          </span>
                        )}
                      </span>
                    </div>
                    {/*
                      Built-ins offer "restore", user modes offer "delete" — the
                      same button position, because to the user both mean "undo
                      what I did to this card". Hidden until hover here; the open
                      panel below shows the same action in plain sight.
                    */}
                    {(mode.built_in ? mode.edited : true) && (
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={fill(
                          t(mode.built_in ? "modes_view.restore_named" : "modes_view.delete_named"),
                          mode.name,
                        )}
                        onClick={(e) => {
                          e.stopPropagation();
                          void remove(mode);
                        }}
                        onKeyDown={(e) => {
                          if (e.key !== "Enter" && e.key !== " ") return;
                          e.stopPropagation();
                          e.preventDefault();
                          void remove(mode);
                        }}
                        className="absolute right-3 top-3 hidden rounded p-1 text-muted-foreground hover:bg-destructive/20 hover:text-destructive group-hover:block"
                      >
                        {mode.built_in ? (
                          <RotateCcw className="h-3.5 w-3.5" />
                        ) : (
                          <Trash2 className="h-3.5 w-3.5" />
                        )}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {shown && (
              <ModeDetail
                mode={shown}
                isActive={shown.slug === active}
                isChosen={shown.slug === chosen}
                overrideName={override ? nameOf(override) : ""}
                switching={switching === shown.slug}
                onUse={() => void choose(shown)}
                onEdit={() => startEditing(shown)}
                onRemove={() => void remove(shown)}
              />
            )}
          </section>

          <section className="flex flex-col gap-4" ref={formRef}>
            <div>
              <h3 className="font-display text-sm font-semibold tracking-tight">
                {t("modes_view.build_title")}
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">{t("modes_view.build_subtitle")}</p>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="flex flex-col gap-3 rounded-xl border border-border bg-secondary/30 p-4">
                <button
                  type="button"
                  onClick={() => void startInterview()}
                  disabled={voiceBusy || connecting}
                  data-testid="mode-interview-button"
                  className={cn(
                    "flex items-center justify-center gap-2 rounded-lg px-4 py-3 font-medium transition-colors",
                    voiceLive
                      ? "bg-destructive/15 text-destructive hover:bg-destructive/25"
                      : "bg-primary text-primary-foreground hover:opacity-90",
                    (voiceBusy || connecting) && "opacity-60",
                  )}
                >
                  {connecting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : voiceLive ? (
                    <MicOff className="h-4 w-4" />
                  ) : (
                    <Mic className="h-4 w-4" />
                  )}
                  {voiceLive ? t("modes_view.interview_end") : t("modes_view.interview_start")}
                </button>
                <p className="text-sm text-muted-foreground">
                  {voiceLive ? t("modes_view.interview_live") : t("modes_view.interview_idle")}
                </p>

                <div className="mt-2 flex flex-col gap-2 border-t border-border pt-3">
                  <label className="text-xs font-medium text-muted-foreground" htmlFor="mode-written">
                    {t("modes_view.rather_type")}
                  </label>
                  <textarea
                    id="mode-written"
                    value={written}
                    onChange={(e) => setWritten(e.target.value)}
                    rows={3}
                    placeholder={t("modes_view.written_placeholder")}
                    className={cn(fieldClass, "resize-none")}
                  />
                  <button
                    type="button"
                    onClick={() => void describeInWriting()}
                    disabled={!written.trim()}
                    className="self-start rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-secondary/60 disabled:opacity-50"
                  >
                    {t("modes_view.ask_in_chat")}
                  </button>
                </div>
              </div>

              <div
                className={cn(
                  "flex flex-col gap-3 rounded-xl border bg-secondary/30 p-4",
                  editing ? "border-primary/60" : "border-border",
                )}
                data-testid="mode-form"
              >
                {editing && (
                  <div className="flex items-start justify-between gap-3 rounded-lg bg-primary/10 px-3 py-2 text-sm">
                    <div>
                      <div className="font-medium">
                        {fill(t("modes_view.editing_title"), editing.name)}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {t(
                          editing.built_in
                            ? "modes_view.editing_hint_builtin"
                            : "modes_view.editing_hint",
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setDraft(EMPTY_DRAFT)}
                      aria-label={t("modes_view.cancel_edit")}
                      title={t("modes_view.cancel_edit")}
                      className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                )}
                <div className="flex gap-2">
                  <input
                    aria-label={t("modes_view.emoji_label")}
                    value={draft.emoji}
                    onChange={(e) => setDraft({ ...draft, emoji: e.target.value })}
                    placeholder="🦉"
                    className={cn(fieldClass, "w-16 text-center")}
                  />
                  <input
                    aria-label={t("modes_view.name_label")}
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                    placeholder={t("modes_view.name_placeholder")}
                    className={cn(fieldClass, "flex-1")}
                  />
                </div>
                <input
                  aria-label={t("modes_view.description_label")}
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  placeholder={t("modes_view.description_placeholder")}
                  className={fieldClass}
                />
                <textarea
                  aria-label={t("modes_view.character_label")}
                  value={draft.character}
                  onChange={(e) => setDraft({ ...draft, character: e.target.value })}
                  rows={editing ? 12 : 5}
                  placeholder={t("modes_view.character_placeholder")}
                  className={cn(fieldClass, "resize-y")}
                />
                <div className="flex flex-wrap gap-2">
                  <BrandedSelect
                    ariaLabel={t("modes_view.length_label")}
                    value={draft.verbosity}
                    onValueChange={(v) => setDraft({ ...draft, verbosity: v as Verbosity })}
                    className="min-w-[180px]"
                    options={(state?.verbosities ?? []).map((v) => ({
                      value: v,
                      label: t(VERBOSITY_KEYS[v]),
                    }))}
                  />
                  <BrandedSelect
                    ariaLabel={t("modes_view.volunteers_label")}
                    value={draft.proactivity}
                    onValueChange={(p) => setDraft({ ...draft, proactivity: p as Proactivity })}
                    className="min-w-[220px]"
                    options={(state?.proactivities ?? []).map((p) => ({
                      value: p,
                      label: t(PROACTIVITY_KEYS[p]),
                    }))}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void create()}
                  disabled={saving}
                  data-testid="mode-save-button"
                  className="flex items-center justify-center gap-2 self-start rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
                >
                  {saving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : editing ? (
                    <Pencil className="h-4 w-4" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                  {editing ? t("modes_view.save_changes") : t("modes_view.save_mode")}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

interface ModeDetailProps {
  mode: AssistantMode;
  isActive: boolean;
  isChosen: boolean;
  /** Name of the mode a section is holding in force, or "" when none is. */
  overrideName: string;
  switching: boolean;
  onUse: () => void;
  onEdit: () => void;
  onRemove: () => void;
}

/**
 * The open card: everything a mode does, in plain sight.
 *
 * The full character text is the point — it is what the assistant actually
 * reads — so it gets the room, verbatim and unabridged. The empty character of
 * the default mode is stated as such rather than shown as a blank box, because
 * "adds nothing" is a fact about that mode, not a missing value.
 */
function ModeDetail({
  mode,
  isActive,
  isChosen,
  overrideName,
  switching,
  onUse,
  onEdit,
  onRemove,
}: ModeDetailProps) {
  const t = useT();
  const buttonClass =
    "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition-colors disabled:opacity-60";
  return (
    <div
      data-testid="mode-detail"
      className="mt-2 flex flex-col gap-4 rounded-xl border border-border bg-secondary/20 p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="text-2xl leading-none">{mode.emoji || "•"}</span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h4 className="font-display text-base font-semibold tracking-tight">{mode.name}</h4>
              <span className="rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {mode.built_in
                  ? mode.edited
                    ? t("modes_view.badge_builtin_edited")
                    : t("modes_view.badge_builtin")
                  : t("modes_view.badge_yours")}
              </span>
              {isActive && (
                <span className="flex items-center gap-1 rounded-md bg-primary/15 px-1.5 py-0.5 text-[11px] font-medium text-primary">
                  <Check className="h-3 w-3" />
                  {t("modes_view.in_use")}
                </span>
              )}
              {!isActive && isChosen && (
                <span className="rounded-md border border-primary/60 px-1.5 py-0.5 text-[11px] font-medium text-primary">
                  {t("modes_view.your_choice")}
                </span>
              )}
            </div>
            {mode.description && (
              <p className="mt-1 text-sm text-muted-foreground">{mode.description}</p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onUse}
            disabled={isActive || switching}
            data-testid="mode-detail-use"
            className={cn(
              buttonClass,
              isActive
                ? "border-primary/40 text-primary"
                : "border-primary bg-primary text-primary-foreground hover:opacity-90",
            )}
          >
            {switching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Check className="h-4 w-4" />
            )}
            {isActive ? t("modes_view.in_use_now") : t("modes_view.use_this_mode")}
          </button>
          <button
            type="button"
            onClick={onEdit}
            data-testid="mode-detail-edit"
            className={cn(buttonClass, "border-border hover:bg-secondary/60")}
          >
            <Pencil className="h-4 w-4" />
            {t("modes_view.edit")}
          </button>
          {(mode.built_in ? mode.edited : true) && (
            <button
              type="button"
              onClick={onRemove}
              data-testid="mode-detail-remove"
              className={cn(
                buttonClass,
                "border-border text-muted-foreground hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive",
              )}
            >
              {mode.built_in ? (
                <RotateCcw className="h-4 w-4" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}
              {mode.built_in ? t("modes_view.restore_original") : t("modes_view.delete")}
            </button>
          )}
        </div>
      </div>

      {!isActive && isChosen && overrideName && (
        <p className="text-sm text-muted-foreground">
          {fill(t("modes_view.detail_waiting"), overrideName)}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-2">
          <h5 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {t("modes_view.detail_behaviour")}
          </h5>
          {mode.character ? (
            <pre
              data-testid="mode-detail-character"
              className="max-h-[28rem] overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-background/60 p-4 font-sans text-sm leading-relaxed text-foreground"
            >
              {mode.character}
            </pre>
          ) : (
            <p
              data-testid="mode-detail-character"
              className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground"
            >
              {t("modes_view.detail_empty_character")}
            </p>
          )}
        </div>
        <dl className="flex flex-col gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("modes_view.length_label")}
            </dt>
            <dd className="mt-0.5">{t(VERBOSITY_KEYS[mode.verbosity])}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("modes_view.volunteers_label")}
            </dt>
            <dd className="mt-0.5">{t(PROACTIVITY_KEYS[mode.proactivity])}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("modes_view.detail_voice")}
            </dt>
            <dd className="mt-0.5">{mode.voice || t("modes_view.detail_voice_default")}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {t("modes_view.detail_id")}
            </dt>
            <dd className="mt-0.5 font-mono text-xs text-muted-foreground">{mode.slug}</dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
