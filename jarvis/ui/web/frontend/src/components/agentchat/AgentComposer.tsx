import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowUp,
  Bot,
  Brain,
  FolderCode,
  FolderOpen,
  Gauge,
  Hammer,
  Mic,
  NotebookPen,
  Paperclip,
  ShieldCheck,
  Square,
} from "lucide-react";

import { Combobox, type ComboboxGroup, type ComboboxOption } from "@/components/ui/combobox";
import { AgentMark } from "@/components/agentic/AgentMark";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useAgentChat, useAgentChatApi } from "@/components/agentchat/AgentChatStoreContext";
import type { ProviderOption } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import { folderLeaf } from "@/lib/folderPath";
import { isApiRunner, pickAgentChatFolder } from "@/lib/agentChatApi";
import { runningTurn } from "@/components/agentchat/reduce";
import { permissionModeIcon } from "@/components/agentchat/permissionIcons";
import { useComposerDictation } from "@/components/agentchat/useComposerDictation";
import { useChatAttachments } from "@/components/agentchat/useChatAttachments";
import { usePasteRescue } from "@/components/agentchat/usePasteRescue";
import { useComposerTypeahead } from "@/components/agentchat/useComposerTypeahead";
import { ComposerTypeahead } from "@/components/agentchat/ComposerTypeahead";
import { ChatAttachmentStrip } from "@/components/agentchat/ChatAttachmentStrip";
import { useAutoGrowTextarea } from "@/hooks/useAutoGrowTextarea";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The composer — the front page's one control for talking to Jarvis by
 * keyboard. What is typed here goes to the same assistant the microphone
 * reaches; the picks under the text box decide what Jarvis runs on for this
 * chat.
 *
 * One card (maintainer sketch, 2026-08-23): the text box on top and, under
 * it, the four picks that decide who answers and how —
 *
 *   Provider · Model · Reasoning effort │ Permission mode · Build | Plan
 *
 * — then dictation and Send on the right. The picks are the backend's
 * catalog for this surface, never a list typed here: the provider column is
 * what that surface may be seated on (connected ones pickable, the rest
 * greyed with a "connect" hint), the model column is the provider's own list
 * (live from its catalog route, or the curated list for a CLI), the effort
 * ladder and the permission ladder are whatever the catalog delivers for
 * that row (jarvis/agent_chat/effort.py, permissions.py) — on the front
 * page one unified ladder for every provider (ask / accept-edits / plan /
 * bypass), each step wearing its stance's glyph (permissionIcons.ts).
 * Build | Plan is the permission ladder's `plan` entry drawn as a switch,
 * shown only when the ladder has one.
 *
 * Which providers a surface offers is the backend's call, not a filter here
 * (jarvis/agent_chat/catalog.py — `rows_for`): the front page's chat lists
 * only providers whose own API Jarvis' harness drives, so a turn there is
 * one `TurnOverride` on one endpoint behind one key (maintainer,
 * 2026-08-26). The vendor CLIs stay where a vendor CLI is the point — the
 * IDE's chat mode, a coding agent in a folder.
 *
 * The provider list is grouped the way the Agents tab groups its cards — by
 * what stands behind a row, never by whether it is connected: a coding CLI
 * signed in with a subscription, a provider's own API behind a key, or a
 * server on this machine with no account at all (maintainer, 2026-08-23: a
 * person must SEE which rows are the CLIs and which are API keys), and it
 * lists only the providers the Agents tab has connected — a fresh install
 * with nothing connected sees every row greyed with a "connect" hint instead.
 *
 * A pick applies to the open session at once (the backend patches it and
 * the next turn runs on it); with no session open it seeds the next one.
 */
export function AgentComposer({ autoFocus = false }: { autoFocus?: boolean }) {
  const t = useT();
  const [value, setValueState] = useState("");
  const textareaRef = useAutoGrowTextarea(value);
  const setValue = useCallback(
    (next: string | ((current: string) => string)) => setValueState(next),
    [],
  );
  const connected = useEventStore((s) => s.connected);
  const wsWarming = useEventStore((s) => s.wsWarming);
  const assistantName = useEventStore((s) => s.assistantName);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const pushToast = useEventStore((s) => s.pushToast);
  const [restarting, setRestarting] = useState(false);

  const store = useAgentChatApi();
  const surface = useAgentChat((s) => s.surface);
  const catalog = useAgentChat((s) => s.catalog);
  const catalogError = useAgentChat((s) => s.catalogError);
  const backendOutdated = useAgentChat((s) => s.backendOutdated);
  const connections = useAgentChat((s) => s.connections);
  const liveModels = useAgentChat((s) => s.liveModels);
  const health = useAgentChat((s) => s.health);
  const draft = useAgentChat((s) => s.draft);
  const activeSessionId = useAgentChat((s) => s.activeSessionId);
  const timeline = useAgentChat((s) => s.timeline);
  const busy = useAgentChat((s) => s.busy);
  const lastError = useAgentChat((s) => s.lastError);
  // The picks this chat cannot take, with the sentence that says why (a pane's
  // CLI chose its provider when it started; a pick it has no typed command
  // for waits for a new chat). A disabled pill that says so is the honest
  // control; one that swallows the click was the reported bug (2026-08-27).
  const locks = useAgentChat((s) => s.locks);
  const setDraft = useAgentChat((s) => s.setDraft);
  const setPlan = useAgentChat((s) => s.setPlan);
  const send = useAgentChat((s) => s.send);
  const cancel = useAgentChat((s) => s.cancel);
  const loadCatalog = useAgentChat((s) => s.loadCatalog);

  useEffect(() => {
    if (!catalog) void loadCatalog();
  }, [catalog, loadCatalog]);

  const providers = useMemo<ProviderOption[]>(
    () => store.getState().providerOptions(),
    // Recompute when either half of the join changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [catalog, connections, store],
  );
  const provider = providers.find((p) => p.id === draft.provider) ?? null;

  const { dictating, stop: stopDictation, toggle: toggleDictation } = useComposerDictation(
    value,
    setValue,
  );

  // Files going in with this message. Held here rather than in the store: they
  // belong to the sentence being typed, and a chat opened elsewhere must not
  // inherit them.
  const [attachError, setAttachError] = useState("");
  const onAttachProblem = useCallback(
    (message: string, severity: "warning" | "error") => {
      setAttachError(severity === "warning" ? t("agent_chat.attach_nothing") : message);
    },
    [t],
  );
  const files = useChatAttachments(
    {
      sessionId: activeSessionId,
      cwd: draft.cwd,
      provider: draft.provider,
      surface,
    },
    onAttachProblem,
  );
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pasteRescue = usePasteRescue();

  // The list over the box after "/", "@" or "$" — which of the three this
  // seat honours is the catalog row's word (`typeahead`), read from the
  // runner, so a seat that would take the slash as text never gets a list.
  const cardRef = useRef<HTMLDivElement | null>(null);
  const triggers = useMemo(() => provider?.typeahead ?? [], [provider]);
  const typeahead = useComposerTypeahead(textareaRef, value, setValueState, {
    surface,
    provider: draft.provider,
    cwd: draft.cwd,
    triggers,
  });

  const running = runningTurn(timeline) !== null;

  async function onSend() {
    const content = value.trim();
    // A message may be files alone: dropping a screenshot and pressing Enter
    // is a complete gesture, and refusing it would be the composer insisting
    // on a sentence the picture already is.
    if ((!content && files.attachments.length === 0) || running || busy) return;
    if (files.analyzing > 0) return; // a file still being read would be sent without its contents
    if (dictating) stopDictation();
    setValueState("");
    setAttachError("");
    const attached = files.attachments;
    files.clear();
    await send(content, attached);
  }

  function onKeyDown(ev: KeyboardEvent<HTMLTextAreaElement>) {
    // Watches for a Ctrl+V the embedded browser may never answer; does nothing
    // in a real browser, where the box pastes on its own.
    pasteRescue.onKeyDown(ev);
    // An open list owns the arrows, Enter, Tab and Escape.
    if (typeahead.onKeyDown(ev)) return;
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      void onSend();
    }
  }

  async function onPickFolder() {
    try {
      const path = await pickAgentChatFolder(draft.cwd || undefined);
      if (path) await setDraft({ cwd: path });
    } catch {
      /* no dialog on this install — the folder stays */
    }
  }

  // The same relauncher the top bar's Restart uses. A 409 means live
  // missions would die — say so and leave the override to the top bar.
  async function onRestart() {
    if (restarting) return;
    setRestarting(true);
    try {
      const res = await fetch("/api/settings/restart-app", { method: "POST" });
      if (res.status === 409) {
        pushToast("warning", t("topbar.restart_missions_running"));
        setRestarting(false);
        return;
      }
      if (!res.ok) throw new Error(`restart-failed:${res.status}`);
      // Success schedules the shutdown; the button stays busy until the
      // window goes away.
    } catch {
      pushToast("error", t("permissions.restart_failed"));
      setRestarting(false);
    }
  }

  // ---- option lists -------------------------------------------------

  // Only what the Agents tab has set up is offered (maintainer, 2026-08-23:
  // the picker lists the providers configured there, nothing else). The one
  // exception is an install with nothing connected yet: then every row shows
  // greyed with its "connect" hint, so the list is a map and not a void. No
  // "active" marker either — that word is the voice sub-agent's, and the
  // pick here is its own thing: what you choose here is what the chat runs.
  const providerGroups = useMemo<ComboboxGroup[]>(() => {
    const anyConnected = providers.some((p) => p.connected);
    const shown = anyConnected ? providers.filter((p) => p.connected) : providers;
    const toOption = (p: ProviderOption): ComboboxOption => {
      // A saved key is not a working one. The live sweep says which seats
      // actually answered a real request; until it lands (or on a backend
      // that has no such route) the row looks exactly as it did before.
      // A coding CLI spends a subscription login, not a key — a leaked
      // `bad_key` (the dual Claude row shares the Anthropic API id) must
      // not paint "Key rejected" on that CLI.
      const live = liveHealthFor(p, health[p.id]);
      const broken = p.connected && live?.status === "error";
      const working = p.connected && live?.status === "ok";
      return {
        value: p.id,
        label: p.label,
        hint: !p.connected
          ? p.cli_installed === false
            ? t("agent_chat.provider_not_installed")
            : t("agent_chat.provider_connect")
          : broken
            ? healthReasonLabel(live?.reason ?? "", t)
            : undefined,
        // Still selectable when broken: a rate limit or a flat network passes,
        // and refusing the click would strand someone whose account recovered
        // a minute ago. The dot and the reason are the honest part.
        disabled: !p.connected,
        icon: (
          <span className="relative inline-flex shrink-0">
            {/* A coding CLI installed on this machine wears its own mark; a
                catalog row wears its provider family's. Both are real brand
                files — the fallback either would otherwise take is a letter
                in a box, and a letter is not a logo. */}
            {p.agentMark ? (
              <AgentMark
                agent={p.agentMark}
                label={p.label}
                logoUrl={p.logoUrl}
                variant="plain"
                size="sm"
              />
            ) : (
              <ProviderLogo providerId={p.id} label={p.label} size="sm" />
            )}
            {(broken || working) && (
              <span
                data-testid={`provider-health-${p.id}`}
                data-health={live?.status}
                title={live?.detail || undefined}
                aria-label={
                  broken
                    ? healthReasonLabel(live?.reason ?? "", t)
                    : t("agent_chat.provider_health_ok")
                }
                className={cn(
                  "absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full ring-2 ring-popover",
                  broken ? "bg-destructive" : "bg-muted-foreground",
                )}
              />
            )}
          </span>
        ),
        searchText: `${p.family} ${p.runner}`,
      };
    };
    const labels: Record<ProviderKind, string> = {
      cli: t("agent_chat.group_clis"),
      api: t("agent_chat.group_api_keys"),
      local: t("agent_chat.group_local"),
    };
    const groups: ComboboxGroup[] = [];
    for (const kind of PROVIDER_KINDS) {
      const rows = shown.filter((p) => providerKind(p) === kind);
      if (rows.length) groups.push({ id: kind, label: labels[kind], options: rows.map(toOption) });
    }
    return groups;
  }, [providers, health, t]);

  const modelList = useMemo(() => {
    if (!provider) return [];
    const live = liveModels[provider.id];
    return provider.models_source === "live" && live && live.length
      ? live
      : (provider.curated_models ?? []);
  }, [provider, liveModels]);

  const modelGroups = useMemo<ComboboxGroup[]>(() => {
    if (!provider) return [];
    const options: ComboboxOption[] = [
      { value: "", label: t("agent_chat.model_default"), hint: provider.label },
      ...modelList
        .filter((m) => m.id)
        .map((m) => ({
          value: m.id,
          label: m.label || m.id,
          hint: m.note || (m.label && m.label !== m.id ? m.id : undefined),
          searchText: m.id,
        })),
    ];
    return [{ id: "models", options }];
  }, [provider, modelList, t]);

  // The effort ladder is the provider's, narrowed to the chosen model's own
  // levels when the catalog knows them (agy's Pro: low/high; its Claude
  // models: none at all, so the pick disappears).
  const effortLevels = useMemo<string[]>(() => {
    if (!provider) return [];
    const model = modelList.find((m) => m.id === draft.model);
    if (model && Array.isArray(model.efforts)) return model.efforts;
    return provider.effort_levels ?? [];
  }, [provider, modelList, draft.model]);

  useEffect(() => {
    // A model change can leave the picked effort off that model's ladder;
    // snap to the nearest lower level (else the first) like the backend does.
    if (!provider || !effortLevels.length || effortLevels.includes(draft.effort)) return;
    const order = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"];
    const idx = order.indexOf(draft.effort);
    const lower = effortLevels.filter((l) => order.indexOf(l) <= idx && order.indexOf(l) >= 0);
    const next = lower.length ? lower[lower.length - 1] : effortLevels[0];
    if (next !== draft.effort) void setDraft({ effort: next });
  }, [provider, effortLevels, draft.effort, setDraft]);

  const effortGroups = useMemo<ComboboxGroup[]>(
    () => [
      {
        id: "effort",
        options: effortLevels.map((lvl) => ({ value: lvl, label: effortLabel(lvl, t) })),
      },
    ],
    [effortLevels, t],
  );

  const permissionModes = useMemo(
    () => (provider?.permission_modes ?? []).filter((m) => m.id !== "plan"),
    [provider],
  );
  const hasPlan = Boolean((provider?.permission_modes ?? []).some((m) => m.id === "plan"));
  const planOn = draft.permissionMode === "plan";
  // Every mode wears its stance's glyph (permissionIcons.ts) in the list and,
  // once picked, on the pill — the Combobox draws the selected option's icon
  // on its trigger, so the column's shield below only shows for a pick the
  // catalog no longer lists.
  const permissionGroups = useMemo<ComboboxGroup[]>(
    () => [
      {
        id: "permission",
        options: permissionModes.map((m) => {
          const Icon = permissionModeIcon(m.id);
          return {
            value: m.id,
            label: m.label,
            searchText: m.description,
            icon: <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />,
          };
        }),
      },
    ],
    [permissionModes],
  );
  // While Plan is on, the permission pill shows the mode Build would return to.
  const permissionValue = planOn ? draft.buildMode || provider?.default_permission_mode || "" : draft.permissionMode;
  const permissionDescription =
    (provider?.permission_modes ?? []).find((m) => m.id === draft.permissionMode)?.description ?? "";

  const canSend =
    connected &&
    (Boolean(value.trim()) || files.attachments.length > 0) &&
    files.analyzing === 0 &&
    !running &&
    !busy &&
    Boolean(provider?.connected);
  const placeholder = connected
    ? t("agent_chat.placeholder")
    : wsWarming
      ? t("voice_state.booting")
      : t("voice_state.offline");

  return (
    <div
      ref={cardRef}
      data-testid="agent-composer"
      data-dragging={files.dragging ? "true" : undefined}
      // The whole card is the drop target, not just the text box: someone
      // dragging a screenshot aims at the composer, and a target smaller than
      // the thing it looks like is a target people miss.
      {...files.dragHandlers}
      className={cn(
        "relative flex flex-col gap-2 rounded-2xl border border-border bg-card p-3 transition-[border-color,box-shadow]",
        "focus-within:border-primary/40",
        files.dragging && "border-primary/60",
      )}
    >
      {files.dragging && (
        <div
          data-testid="composer-drop-overlay"
          aria-hidden
          className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-2xl border-2 border-dashed border-primary/70 bg-card/90 text-sm font-medium text-primary"
        >
          {t("agent_chat.attach_drop_hint")}
        </div>
      )}
      {dictating && (
        <div
          className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-primary"
          role="status"
          aria-live="polite"
        >
          <span className="relative flex h-2 w-2" aria-hidden>
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/70" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
          </span>
          <span className="font-medium">{t("chats_view.dictation_listening")}</span>
        </div>
      )}
      <ChatAttachmentStrip
        attachments={files.attachments}
        analyzing={files.analyzing}
        onRemove={files.remove}
      />
      <ComposerTypeahead
        anchorRef={cardRef}
        open={typeahead.open}
        trigger={typeahead.token?.trigger ?? null}
        items={typeahead.items}
        loading={typeahead.loading}
        activeIndex={typeahead.activeIndex}
        onHover={typeahead.setActiveIndex}
        onPick={typeahead.pick}
      />
      <textarea
        ref={textareaRef}
        data-jarvis-chat-input=""
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => setValueState(e.target.value)}
        onKeyDown={onKeyDown}
        onSelect={typeahead.refresh}
        onClick={typeahead.refresh}
        onBlur={typeahead.blur}
        aria-expanded={typeahead.open || undefined}
        aria-autocomplete={triggers.length ? "list" : undefined}
        onPaste={(e) => {
          pasteRescue.onPaste();
          files.onPaste(e);
        }}
        placeholder={placeholder}
        disabled={!connected}
        rows={2}
        // Grows with the text up to half the window (useAutoGrowTextarea), so
        // a message is read whole while it is written; past the cap the box
        // scrolls rather than pushing the picks and Send out of reach.
        className="max-h-[50vh] w-full resize-none bg-transparent px-1 py-1 text-[15px] leading-relaxed text-foreground scrollbar-jarvis placeholder:text-muted-foreground focus-visible:outline-none disabled:opacity-50"
      />
      <div className="flex flex-wrap items-center gap-1">
        {/*
          Who you are talking to, before what they run on. The two chats wear
          the same face, so without this the front page and the IDE's chat are
          indistinguishable at a glance — and they are not the same thing: here
          it is Jarvis with a keyboard, there it is a coding agent in a folder
          (maintainer, 2026-08-25). Not a control: what a chat IS cannot be
          switched from inside the chat.
        */}
        <span
          data-testid="composer-surface"
          data-surface={surface}
          title={
            surface === "jarvis"
              ? t("agent_chat.surface_jarvis_hint")
              : `${t("agent_chat.surface_agent_hint")}: ${draft.cwd || folderLeaf("")}`
          }
          className="inline-flex h-7 max-w-[180px] shrink-0 items-center gap-1.5 rounded-lg bg-secondary/60 px-2 text-xs font-medium text-foreground"
        >
          {surface === "jarvis" ? (
            <img
              src="/jarvis-logo.png"
              width={14}
              height={14}
              alt=""
              aria-hidden
              className="h-3.5 w-3.5 shrink-0"
            />
          ) : (
            <FolderCode className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          )}
          <span className="truncate">
            {surface === "jarvis" ? assistantName : t("agent_chat.surface_agent")}
          </span>
        </span>
        <Pick
          testId="composer-provider"
          ariaLabel={t("agent_chat.pick_provider")}
          value={draft.provider}
          groups={providerGroups}
          onChange={(v) => void setDraft({ provider: v })}
          fallbackLabel={catalogError ? t("agent_chat.catalog_unavailable") : t("agent_chat.pick_provider")}
          searchPlaceholder={t("agent_chat.search_providers")}
          icon={provider ? undefined : <Bot className="h-3.5 w-3.5" aria-hidden />}
          disabled={!catalog || Boolean(locks?.provider)}
          title={locks?.provider}
        />
        <Pick
          testId="composer-model"
          ariaLabel={t("agent_chat.pick_model")}
          value={draft.model}
          groups={modelGroups}
          onChange={(v) => void setDraft({ model: v })}
          fallbackLabel={draft.model || t("agent_chat.model_default")}
          searchPlaceholder={t("agent_chat.search_models")}
          icon={<Brain className="h-3.5 w-3.5" aria-hidden />}
          disabled={!provider || Boolean(locks?.model)}
          title={locks?.model}
          className="max-w-[220px]"
        />
        {provider && effortLevels.length > 1 && (
          <Pick
            testId="composer-effort"
            ariaLabel={t("agent_chat.pick_effort")}
            value={draft.effort}
            groups={effortGroups}
            onChange={(v) => void setDraft({ effort: v })}
            fallbackLabel={effortLabel(draft.effort, t)}
            icon={<Gauge className="h-3.5 w-3.5" aria-hidden />}
            disabled={Boolean(locks?.effort)}
            title={locks?.effort}
          />
        )}
        {provider && permissionModes.length > 0 && (
          <>
            <span className="mx-1 h-4 w-px bg-border" aria-hidden />
            <Pick
              testId="composer-permission"
              ariaLabel={t("agent_chat.pick_permission")}
              value={permissionValue}
              groups={permissionGroups}
              onChange={(v) => void setDraft({ permissionMode: v })}
              fallbackLabel={t("agent_chat.pick_permission")}
              icon={<ShieldCheck className="h-3.5 w-3.5" aria-hidden />}
              disabled={Boolean(locks?.permissionMode)}
              title={locks?.permissionMode ?? permissionDescription}
              className="max-w-[220px]"
            />
          </>
        )}
        {hasPlan && (
          <button
            type="button"
            role="switch"
            aria-checked={planOn}
            data-testid="composer-plan"
            onClick={() => void setPlan(!planOn)}
            disabled={Boolean(locks?.permissionMode)}
            title={
              locks?.permissionMode ?? (planOn ? t("agent_chat.plan_hint") : t("agent_chat.build_hint"))
            }
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-xs font-medium transition-colors disabled:opacity-50",
              planOn
                ? "bg-primary/15 text-primary hover:bg-primary/20"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {planOn ? (
              <NotebookPen className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <Hammer className="h-3.5 w-3.5" aria-hidden />
            )}
            {planOn ? t("agent_chat.mode_plan") : t("agent_chat.mode_build")}
          </button>
        )}
        {/*
          No folder chip on the Jarvis surface. Someone talking to Jarvis is
          not pointing it at a checkout: the chip belongs to the IDE's chat,
          and here it only ever showed the leaf of the default path — on
          Windows the account name, which reads like a setting nobody chose.
          The folder itself stays: a CLI seat needs a directory to start in,
          and the surface brings its own (`surface_kits.py`) instead of the
          home directory (maintainer, 2026-08-25).
        */}
        {surface !== "jarvis" && (
          <button
            type="button"
            onClick={() => void onPickFolder()}
            title={
              draft.cwd ? `${t("agent_chat.surface_agent_hint")}: ${draft.cwd}` : t("agent_chat.folder")
            }
            aria-label={t("agent_chat.folder")}
            data-testid="composer-folder"
            className="inline-flex h-7 max-w-[160px] items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <FolderOpen className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="hidden truncate font-mono text-[11px] 2xl:inline">{folderLeaf(draft.cwd)}</span>
          </button>
        )}
        <span className="flex-1" />
        {/*
          Dropping and pasting are the primary paths; this is for the people
          who do neither — and for a browser tab, where a drag out of Explorer
          may carry nothing the page can read.
        */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="sr-only"
          tabIndex={-1}
          onChange={(event) => {
            const picked = Array.from(event.currentTarget.files ?? []);
            event.currentTarget.value = "";
            if (picked.length > 0) files.attachFiles(picked);
          }}
        />
        <button
          type="button"
          data-testid="composer-attach"
          onClick={() => fileInputRef.current?.click()}
          disabled={!connected}
          aria-label={t("agent_chat.attach")}
          title={t("agent_chat.attach_hint")}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-50"
        >
          <Paperclip className="h-4 w-4" />
        </button>
        <button
          type="button"
          data-jarvis-dictation-trigger
          onClick={toggleDictation}
          disabled={!connected}
          aria-label={dictating ? t("chats_view.dictation_stop") : t("chats_view.dictation_start")}
          title={dictating ? t("chats_view.dictation_stop") : t("chats_view.dictation_start")}
          className={cn(
            "inline-flex h-8 w-8 items-center justify-center rounded-lg border transition-colors disabled:opacity-50",
            dictating
              ? "animate-jarvis-pulse border-primary/50 bg-primary/15 text-primary"
              : "border-transparent text-muted-foreground hover:bg-secondary hover:text-foreground",
          )}
        >
          {dictating ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
        </button>
        {running ? (
          <button
            type="button"
            onClick={() => void cancel()}
            aria-label={t("agent_chat.stop")}
            title={t("agent_chat.stop")}
            data-testid="composer-stop"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-foreground text-background transition-colors hover:bg-foreground/90"
          >
            <Square className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void onSend()}
            disabled={!canSend}
            aria-label={t("agent_chat.send")}
            data-testid="composer-send"
            className={cn(
              "inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors",
              canSend
                ? "bg-primary text-primary-foreground hover:bg-primary/90"
                : "bg-secondary text-muted-foreground/60",
            )}
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        )}
      </div>
      {backendOutdated && (
        <div
          className="flex items-center gap-2 px-1 text-xs text-muted-foreground"
          data-testid="composer-backend-outdated"
          role="status"
        >
          <span>{t("agent_chat.backend_outdated")}</span>
          <button
            type="button"
            onClick={() => void onRestart()}
            disabled={restarting}
            className="font-medium text-primary hover:underline disabled:opacity-60"
          >
            {t("agent_chat.restart_now")}
          </button>
        </div>
      )}
      {provider && !provider.connected && (
        <div
          className="flex items-center gap-2 px-1 text-xs text-muted-foreground"
          data-testid="composer-connect-hint"
        >
          <span>
            {fill(
              provider.cli_installed === false
                ? t("agent_chat.hint_not_installed")
                : t("agent_chat.hint_connect"),
              { provider: provider.label },
            )}
          </span>
          <button
            type="button"
            onClick={() => setActiveSection("apikeys")}
            className="font-medium text-primary hover:underline"
          >
            {t("agent_chat.open_api_keys")}
          </button>
        </div>
      )}
      {attachError && (
        <div
          className="px-1 text-xs text-destructive"
          role="alert"
          data-testid="composer-attach-error"
        >
          {attachError}
        </div>
      )}
      {lastError && (
        <div className="px-1 text-xs text-destructive" role="alert" data-testid="composer-error">
          {lastError}
        </div>
      )}
    </div>
  );
}

/**
 * What stands behind a provider row: a coding CLI (a subscription login, no
 * key), a provider's own API behind a key, or a server on this machine with
 * no account. Decided from the catalog's own facts (runner, keyless) — the
 * same split the Agents tab draws, so the two never disagree. Claude's dual
 * row lands where its resolved runner says: the CLI group when Claude Code
 * answers, the API-key group when the Anthropic endpoint does.
 *
 * `brain` counts as an API seat, not a CLI one: it is Jarvis' own harness
 * calling that provider's endpoint with that provider's key, which is what
 * every row on the front page's chat resolves to. Reading it as a CLI put
 * the whole list under the wrong heading there.
 */
/**
 * A health reason in the person's own words. The backend's `reason` is a
 * machine token ("bad_key", "no_credits", …) from the same vocabulary the
 * API-Keys screen uses; an unfamiliar one falls back to the general "not
 * answering" rather than showing a word out of a log.
 */
export function healthReasonLabel(reason: string, t: (key: string) => string): string {
  switch (reason) {
    case "bad_key":
      return t("agent_chat.provider_health_bad_key");
    case "no_credits":
    case "rate_limited":
      return t("agent_chat.provider_health_no_credits");
    case "unreachable":
      return t("agent_chat.provider_health_unreachable");
    case "timeout":
      return t("agent_chat.provider_health_slow");
    default:
      return t("agent_chat.provider_health_error");
  }
}

/** Reasons that only make sense for a stored API key. */
export function isApiKeyHealthReason(reason: string): boolean {
  return reason === "bad_key";
}

export type ProviderKind = "cli" | "api" | "local";
export const PROVIDER_KINDS: readonly ProviderKind[] = ["cli", "api", "local"];
export function providerKind(p: { runner: string; keyless: boolean }): ProviderKind {
  if (p.keyless) return "local";
  return isApiRunner(p.runner) ? "api" : "cli";
}

function liveHealthFor(
  p: { runner: string; keyless: boolean },
  live: { status: string; reason: string; detail: string } | undefined,
): { status: string; reason: string; detail: string } | undefined {
  if (!live) return undefined;
  if (providerKind(p) === "cli" && isApiKeyHealthReason(live.reason)) return undefined;
  return live;
}

/** One compact pick on the composer's bottom row — a pill-sized Combobox. */
function Pick({
  testId,
  ariaLabel,
  value,
  groups,
  onChange,
  fallbackLabel,
  searchPlaceholder,
  icon,
  disabled,
  title,
  className,
}: {
  testId: string;
  ariaLabel: string;
  value: string;
  groups: ComboboxGroup[];
  onChange: (value: string) => void;
  fallbackLabel?: string;
  searchPlaceholder?: string;
  icon?: React.ReactNode;
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  // The Combobox draws the selected option's own icon; the leading glyph here
  // is the column's, shown when the option has none (model, effort, permission).
  const selectedHasIcon = groups.some((g) => g.options.some((o) => o.value === value && o.icon));
  return (
    <span className={cn("inline-flex min-w-0 items-center", className)} title={title}>
      {icon && !selectedHasIcon && (
        <span className="-mr-6 ml-2 shrink-0 text-muted-foreground" aria-hidden>
          {icon}
        </span>
      )}
      <Combobox
        value={value}
        groups={groups}
        onChange={onChange}
        ariaLabel={ariaLabel}
        fallbackLabel={fallbackLabel}
        searchPlaceholder={searchPlaceholder}
        disabled={disabled}
        testId={testId}
        triggerHint={false}
        className={cn(
          "h-7 w-auto max-w-[200px] gap-1.5 rounded-lg border-transparent bg-transparent py-0 pr-1.5 text-xs font-medium text-foreground shadow-none",
          "hover:border-transparent hover:bg-secondary focus-visible:ring-1",
          icon && !selectedHasIcon ? "pl-7" : "pl-1.5",
        )}
      />
    </span>
  );
}

export function effortLabel(level: string, t: (key: string) => string): string {
  switch (level) {
    case "":
      return t("agent_chat.effort_default");
    case "none":
      return t("agent_chat.effort_none");
    case "minimal":
      return t("agent_chat.effort_minimal");
    case "low":
      return t("agent_chat.effort_low");
    case "medium":
      return t("agent_chat.effort_medium");
    case "high":
      return t("agent_chat.effort_high");
    case "xhigh":
      return t("agent_chat.effort_xhigh");
    case "max":
      return t("agent_chat.effort_max");
    case "ultra":
      return t("agent_chat.effort_ultra");
    default:
      return level;
  }
}
