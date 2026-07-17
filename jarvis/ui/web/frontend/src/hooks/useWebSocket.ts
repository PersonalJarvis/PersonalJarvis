import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { WSClient } from "@/lib/ws";
import {
  SECTION_LABELS,
  isSectionId,
  useEventStore,
  type ChatMessage,
  type VoiceState,
} from "@/store/events";
import { useSubAgentStore, SUB_AGENT_EVENT_NAMES } from "@/store/jarvisAgents";
import { WSEventEnvelope, WSWelcome } from "@/schema/ws";
import { useI18nStore, hydrateUiLanguage, hydrateReplyLanguage, translate } from "@/i18n";

let singleton: WSClient | null = null;

export function getWSClient(): WSClient | null {
  return singleton;
}

/**
 * Mount-point for the WS connection. Updates the Zustand store as events arrive.
 * Designed to be called once from <App />; subsequent calls short-circuit.
 *
 * Event normalization: server sends envelope field `event_name`, `timestamp_ns`,
 * `source_layer`; we map those to the UI store's `name`, `ts`, `layer` fields.
 * Voice-state changes ride on `SystemStateChanged` with `payload.new_state` in
 * all-caps — we lowercase it to match the VoiceState enum.
 */
export function useWebSocket(): void {
  const mounted = useRef(false);
  const queryClient = useQueryClient();
  const setConnected = useEventStore((s) => s.setConnected);
  const setWarming = useEventStore((s) => s.setWarming);
  const pushEvent = useEventStore((s) => s.pushEvent);
  const setVoice = useEventStore((s) => s.setVoice);
  const setVoiceReady = useEventStore((s) => s.setVoiceReady);
  const setTranscription = useEventStore((s) => s.setTranscription);
  const pushMessage = useEventStore((s) => s.pushMessage);
  const setChatThinking = useEventStore((s) => s.setChatThinking);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const setBrainProvider = useEventStore((s) => s.setBrainProvider);
  const pushToast = useEventStore((s) => s.pushToast);

  useEffect(() => {
    if (mounted.current) return;
    mounted.current = true;

    const client = new WSClient({
      // `connected` is welcome-gated (see the welcome branch below), so a raw
      // socket open must NOT mark connected — the fast-boot bootstrap also
      // opens then closes with 1013 without ever sending a welcome frame.
      onOpen: () => {},
      onClose: (code, info) => {
        // 1013 = bootstrap "try again later" → backend still warming, not down.
        // A 4401 with a successful ticket mint (WebKit cookie-less handshake,
        // BUG-065) is equally transient: the next attempt already carries a
        // fresh credential, so keep the "starting" state instead of flashing
        // OFFLINE. A 4401 whose mint failed means the session is dead — that
        // one falls through to the honest offline state.
        setWarming(code === 1013 || Boolean(info?.authRetryPending));
        setConnected(false);
      },
      onMessage: (raw) => {
        const welcome = WSWelcome.safeParse(raw);
        if (welcome.success) {
          // The real app sends `welcome` immediately after accepting the socket;
          // this — not the raw open — is the authoritative "connected" signal.
          setConnected(true);
          setWarming(false);
          // Re-seed voiceReady on EVERY (re)connect. VoiceBootStatus is a
          // one-shot bus event, so a socket that (re)connects after readiness
          // already flipped — e.g. the fast-boot 1013 reconnect — or whose
          // one-time mount-seed (useVoiceStatus) failed would otherwise keep a
          // stale value and leave the banner stuck on "starting up". This makes
          // the REST mirror the authoritative source on each connect.
          void fetch("/api/voice/status")
            .then((r) => (r.ok ? r.json() : null))
            .then((data) => {
              if (data && typeof data.ready === "boolean") setVoiceReady(data.ready);
            })
            .catch(() => {
              // Offline / headless: keep the current value; the live
              // VoiceBootStatus event still updates it if/when it arrives.
            });
          return;
        }

        const parsed = WSEventEnvelope.safeParse(raw);
        if (!parsed.success) return;
        const env = parsed.data;

        pushEvent({
          id: `${env.timestamp_ns}-${env.trace_id.slice(0, 8)}`,
          name: env.event_name,
          layer: env.source_layer,
          // Backend sends wall-clock nanoseconds (time.time_ns()); JS Date wants
          // milliseconds. Without this divide new Date(ts) is "Invalid Date".
          ts: Math.floor(env.timestamp_ns / 1_000_000),
          trace_id: env.trace_id,
          payload: env.payload,
        });

        // Live reasoning trace: while the text chat is waiting on a reply,
        // turn-progress events (tools, computer-use, worker dispatch, ...)
        // become visible thinking steps. Gated on chatThinking inside the
        // store, so this is a cheap no-op for every other event.
        useEventStore
          .getState()
          .ingestThinkingEvent(
            env.event_name,
            env.payload,
            Math.floor(env.timestamp_ns / 1_000_000),
          );

        // Jarvis-Agents dashboard: build the live tree from the Phase-5.5 events.
        if (SUB_AGENT_EVENT_NAMES.has(env.event_name)) {
          useSubAgentStore
            .getState()
            .ingestEvent(env.event_name, env.trace_id, env.timestamp_ns, env.payload);
        }

        if (env.event_name === "SystemStateChanged") {
          const p = env.payload as { new_state?: unknown; previous?: unknown };
          const state = p.new_state;
          if (typeof state === "string") {
            const lower = state.toLowerCase();
            if (isVoiceState(lower)) setVoice(lower);
            // The live-transcript box has no other reset path: without this,
            // the last utterance of a session survives into READY/IDLE and the
            // next session, masquerading as a frozen live transcript (live
            // incidents 2026-07-15/16: a stale "Was" sat in the sidebar long
            // after its session ended). Clear at every session boundary —
            // entering IDLE (session over) and leaving IDLE (fresh session).
            const previous =
              typeof p.previous === "string" ? p.previous.toLowerCase() : "";
            if (lower === "idle" || previous === "idle") {
              setTranscription("", true);
            }
          }
        }

        // The voice feature warms up ~20s after the window connects; the
        // backend announces readiness over this envelope. Drives the sidebar
        // "Voice starting…" indicator. payload: { ready: boolean, detail: string }.
        if (env.event_name === "VoiceBootStatus") {
          const ready = (env.payload as { ready?: unknown }).ready;
          if (typeof ready === "boolean") setVoiceReady(ready);
        }

        if (env.event_name === "MessageSent") {
          const p = env.payload as {
            role?: string;
            text?: string;
            thread_id?: string;
            source_layer?: string;
          };
          if (
            p.role &&
            p.text &&
            (p.role === "user" ||
              p.role === "assistant" ||
              p.role === "system" ||
              p.role === "preamble")
          ) {
            const msg: ChatMessage = {
              id: `${env.timestamp_ns}-${env.trace_id.slice(0, 8)}`,
              role: p.role,
              content: p.text,
              // ns → ms (see EventItem mapping above).
              ts: Math.floor(env.timestamp_ns / 1_000_000),
              thread_id: p.thread_id,
            };
            pushMessage(msg);
            console.log("[ChatThinking] MessageSent role=", p.role);
            // Brain reply (or system diagnostic) has arrived — thinking off.
            // The "preamble" role is the Flash-Brain pre-ack; it does NOT end
            // the thinking state because the assistant's main reply is still
            // pending. Only "assistant" / "system" clear the indicator.
            if (p.role === "assistant") {
              // Snapshot the live reasoning trace onto this reply so the
              // "Thought for Xs" disclosure can replay it. Also clears the flag.
              useEventStore.getState().finishThinking(msg.id);
              console.log("[ChatThinking] reply → false");
            } else if (p.role === "system") {
              setChatThinking(false);
              console.log("[ChatThinking] reply → false");
            }
          }
        }

        if (env.event_name === "ErrorOccurred") {
          // Brain errors abort the wait cycle, otherwise the indicator hangs
          // until the 60s timeout. We ignore other layer errors here.
          const p = env.payload as { layer?: string; source_layer?: string };
          if (p.layer === "brain" || p.source_layer === "brain") {
            setChatThinking(false);
            console.log("[ChatThinking] brain-error → false");
          }
        }

        if (env.event_name === "TranscriptionUpdate") {
          const p = env.payload as { text?: string; is_final?: boolean };
          if (typeof p.text === "string") {
            setTranscription(p.text, Boolean(p.is_final));
          }
        }

        if (env.event_name === "DictationTranscript") {
          // Chat mic-dictation — transcribe-only. Interim partials overwrite the
          // live tail; the final one is committed (appended to the chat input).
          // Separate from TranscriptionUpdate so live-voice transcripts never
          // leak into the text box. Uses getState() to stay out of the deps array.
          const p = env.payload as { text?: string; is_final?: boolean };
          const text = typeof p.text === "string" ? p.text : "";
          if (p.is_final) {
            useEventStore.getState().commitDictation(text);
          } else {
            useEventStore.getState().setDictationInterim(text);
          }
        }

        if (env.event_name === "NavigateSidebar") {
          const p = env.payload as { section?: string };
          if (isSectionId(p.section)) {
            setActiveSection(p.section);
            pushToast(
              "info",
              `${translate("use_web_socket.jarvis_opened")} ${SECTION_LABELS[p.section]}`,
            );
          }
        }

        if (env.event_name === "BrainProviderSwitched") {
          const p = env.payload as { to_provider?: string; from_provider?: string };
          if (typeof p.to_provider === "string") {
            setBrainProvider(p.to_provider);
            pushToast("success", `Brain → ${p.to_provider}`);
            // The switch payload carries no model, so re-fetch the authoritative
            // status (provider + model) — keeps the sidebar model line fresh
            // after a voice/UI provider switch. useBrainStatus listens for this.
            window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
          }
        }

        if (env.event_name === "SecretConfigured") {
          // Trigger only — ApiKeysView refreshes its own provider list.
          window.dispatchEvent(new CustomEvent("jarvis:secret-configured", { detail: env.payload }));
        }

        // Live interface-language switch (voice / Control API / another client):
        // the whole app re-renders in the new language with no reload. push:false
        // so receiving the broadcast does not echo a PUT back.
        if (env.event_name === "UiLanguageChanged") {
          const p = env.payload as { language?: string };
          if (p.language === "en" || p.language === "de" || p.language === "es") {
            useI18nStore.getState().setUi(p.language, { push: false });
          }
        }

        // A voice command / the Control API writes config via the atomic writer,
        // which fires ConfigReloaded (not UiLanguageChanged). Re-hydrate the
        // affected language setting so the UI reflects it live.
        if (env.event_name === "ConfigReloaded") {
          const p = env.payload as { changed_keys?: unknown };
          const keys = Array.isArray(p.changed_keys) ? (p.changed_keys as string[]) : [];
          if (keys.includes("ui.language")) void hydrateUiLanguage();
          if (keys.includes("brain.reply_language")) void hydrateReplyLanguage();
        }

        if (env.event_name === "ToastNotification") {
          const p = env.payload as { kind?: string; message?: string };
          if (typeof p.message === "string") {
            const kind = p.kind === "error" || p.kind === "warning" || p.kind === "success"
              ? p.kind
              : "info";
            pushToast(kind, p.message);
          }
        }

        if (env.event_name === "DocIndexReloaded") {
          // The registry can reload while the reader is not mounted. Invalidate
          // the complete docs cache here so navigation, details, and search all
          // reflect the same index the next time they are shown.
          void queryClient.invalidateQueries({ queryKey: ["docs"] });
        }

        if (env.event_name === "ActionApprovalRequired") {
          const p = env.payload as {
            mission_id?: unknown;
            tool_name?: unknown;
          };
          if (
            typeof p.mission_id === "string" &&
            p.mission_id.length > 0 &&
            typeof p.tool_name === "string" &&
            p.tool_name.length > 0
          ) {
            pushToast(
              "warning",
              translate("mission_tool_approvals.toast_pending")
                .replace("{tool}", p.tool_name)
                .replace("{mission}", p.mission_id),
            );
            window.dispatchEvent(
              new CustomEvent("jarvis:mission-tool-approval", { detail: p }),
            );
          }
        }

        if (env.event_name === "AchievementUnlocked") {
          const p = env.payload as {
            achievement_id?: string;
            title?: string;
            tier?: string;
          };
          if (typeof p.title === "string" && p.title.length > 0) {
            pushToast("success", `Achievement: ${p.title}`);
          }
          // Local custom event: AchievementGrid listens for it and invalidates
          // the React Query list, so the unlock becomes visible immediately.
          window.dispatchEvent(
            new CustomEvent("jarvis:achievement-unlocked", { detail: p }),
          );
        }
      },
    });
    client.connect();
    singleton = client;

    return () => {
      client.close();
      singleton = null;
      mounted.current = false;
    };
  }, [
    setConnected,
    setWarming,
    pushEvent,
    setVoice,
    setVoiceReady,
    setTranscription,
    pushMessage,
    setChatThinking,
    setActiveSection,
    setBrainProvider,
    pushToast,
    queryClient,
  ]);
}

function isVoiceState(v: unknown): v is VoiceState {
  return v === "idle" || v === "listening" || v === "thinking" || v === "speaking" || v === "error";
}
