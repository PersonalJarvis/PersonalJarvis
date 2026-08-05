/**
 * The composer — one box that says what it will do before you press enter.
 *
 * Everything that decides where a prompt LANDS sits on the box itself: the
 * project, whether the work runs locally, the branch, which coding CLI, which
 * model, and how much it is allowed to do without asking. The old surface
 * spread those across a wizard, a toolbar and a settings screen, so the honest
 * answer to "where is this going?" required three screens.
 *
 * Two microphone buttons, deliberately, because they do different things:
 *
 * * **Dictate** turns speech into TEXT in this box. Nothing is sent; the words
 *   land where the caret is and you edit them like anything you typed.
 * * **Talk** opens a spoken conversation with the assistant, which is free to
 *   answer, ask back, and compose the prompt for you.
 *
 * One icon for both would have to guess which one the user meant, and guessing
 * wrong either swallows a sentence or starts a call nobody asked for.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AudioLines,
  ChevronDown,
  GitBranch,
  Folder,
  Loader2,
  Mic,
  Monitor,
  Paperclip,
  ArrowUp,
} from "lucide-react";

import { AgentMark } from "@/components/agentic/AgentMark";
import { cn } from "@/lib/utils";

export interface ComposerAgent {
  /** Registry id — `claude`, `codex`, … Never branched on outside the picker. */
  id: string;
  label: string;
  models: readonly { id: string; label: string }[];
}

export interface ChatComposerProps {
  projectName: string;
  /** Branch of the project's folder, when it is a git repository. */
  branch?: string | null;
  agents: readonly ComposerAgent[];
  agentId: string;
  modelId: string | null;
  onAgentChange: (agentId: string) => void;
  onModelChange: (modelId: string) => void;
  onSend: (text: string) => void | Promise<void>;
  /** Start dictation into this box. Absent = no speech-to-text on this device. */
  onDictate?: () => void;
  /** Open a spoken conversation. Absent = voice is not usable on this device. */
  onTalk?: () => void;
  onAttach?: () => void;
  busy?: boolean;
  placeholder?: string;
}

export function ChatComposer({
  projectName,
  branch,
  agents,
  agentId,
  modelId,
  onAgentChange,
  onModelChange,
  onSend,
  onDictate,
  onTalk,
  onAttach,
  busy = false,
  placeholder = "Just start typing…",
}: ChatComposerProps) {
  const [text, setText] = useState("");
  const [menu, setMenu] = useState<"agent" | "model" | null>(null);
  const box = useRef<HTMLTextAreaElement | null>(null);

  const agent = agents.find((a) => a.id === agentId) ?? agents[0];
  const model = agent?.models.find((m) => m.id === modelId) ?? agent?.models[0];

  /*
   * Grow with the text, up to a ceiling.
   *
   * Measured from `scrollHeight` after a reset to `auto`: without the reset the
   * box can only ever grow, because `scrollHeight` of an already-tall element
   * never reports that its content shrank. That is the whole bug behind
   * composers that stay ten lines high after you delete the paragraph.
   */
  useEffect(() => {
    const node = box.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
  }, [text]);

  const submit = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    void onSend(trimmed);
    setText("");
  }, [busy, onSend, text]);

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4" data-testid="chat-composer">
      {/* Where this is going, above the box rather than buried in a menu. */}
      <div className="mb-1.5 flex items-center gap-3 px-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <Folder className="h-3 w-3" aria-hidden />
          {projectName}
        </span>
        <span className="flex items-center gap-1.5">
          <Monitor className="h-3 w-3" aria-hidden />
          Local
        </span>
        {branch && (
          <span className="flex items-center gap-1.5">
            <GitBranch className="h-3 w-3" aria-hidden />
            {branch}
          </span>
        )}
      </div>

      <div
        className={cn(
          "rounded-xl border border-border bg-card/70 shadow-lg backdrop-blur",
          "focus-within:border-primary/50",
        )}
      >
        <textarea
          ref={box}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line — the convention every
            // chat box in this class uses, so muscle memory transfers.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder={placeholder}
          aria-label="Message"
          data-dictation-target="true"
          className="block max-h-[220px] w-full resize-none bg-transparent px-4 pb-2 pt-3.5 text-sm outline-none placeholder:text-muted-foreground/60"
        />

        <div className="flex items-center gap-1 px-2 pb-2">
          <IconAction icon={Paperclip} label="Attach files" onClick={onAttach} />

          <div className="flex-1" />

          <Chip
            open={menu === "agent"}
            onToggle={() => setMenu((m) => (m === "agent" ? null : "agent"))}
            icon={
              agent ? (
                <AgentMark
                  agent={agent.id}
                  label={agent.label}
                  size="sm"
                  className="h-4 w-4 rounded-[3px] border-0 bg-transparent"
                />
              ) : undefined
            }
            label={agent?.label ?? "Pick an agent"}
            options={agents.map((a) => ({ id: a.id, label: a.label }))}
            onPick={(value) => {
              onAgentChange(value);
              setMenu(null);
            }}
            align="right"
          />

          <Chip
            open={menu === "model"}
            onToggle={() => setMenu((m) => (m === "model" ? null : "model"))}
            label={model?.label ?? "Default model"}
            options={(agent?.models ?? []).map((m) => ({ id: m.id, label: m.label }))}
            onPick={(value) => {
              onModelChange(value);
              setMenu(null);
            }}
            align="right"
          />

          {/* Speech to TEXT — the words land in this box and go nowhere else. */}
          <IconAction
            icon={Mic}
            label="Dictate into this box"
            onClick={onDictate}
            testId="composer-dictate"
          />
          {/* Speech to the ASSISTANT — a conversation, not a transcription. */}
          <IconAction
            icon={AudioLines}
            label="Talk to the assistant"
            onClick={onTalk}
            testId="composer-talk"
            accent
          />

          <button
            type="button"
            onClick={submit}
            disabled={!text.trim() || busy}
            aria-label="Send"
            title="Send — Enter"
            className={cn(
              "ml-0.5 flex h-7 w-7 items-center justify-center rounded-full transition-colors",
              "bg-primary text-primary-foreground disabled:bg-muted disabled:text-muted-foreground",
            )}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <ArrowUp className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function IconAction({
  icon: Icon,
  label,
  onClick,
  testId,
  accent,
}: {
  icon: typeof Mic;
  label: string;
  onClick?: () => void;
  testId?: string;
  accent?: boolean;
}) {
  // A button with no handler is not rendered at all rather than rendered dead:
  // an icon that does nothing when clicked is worse than a missing one, because
  // the user cannot tell whether the feature is absent or broken.
  if (!onClick) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      data-testid={testId}
      className={cn(
        "flex h-7 w-7 items-center justify-center rounded-md transition-colors",
        accent
          ? "text-primary hover:bg-primary/15"
          : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
      )}
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}

function Chip({
  open,
  onToggle,
  icon,
  label,
  options,
  onPick,
  align = "left",
}: {
  open: boolean;
  onToggle: () => void;
  icon?: React.ReactNode;
  label: string;
  options: readonly { id: string; label: string }[];
  onPick: (id: string) => void;
  align?: "left" | "right";
}) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground"
      >
        {icon}
        <span className="max-w-[180px] truncate">{label}</span>
        <ChevronDown className="h-3 w-3 shrink-0" aria-hidden />
      </button>
      {open && options.length > 0 && (
        <div
          className={cn(
            "absolute bottom-8 z-30 max-h-64 w-56 overflow-y-auto rounded-md border border-border bg-popover py-1 shadow-xl",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => onPick(option.id)}
              className="flex w-full items-center px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-accent/60"
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
