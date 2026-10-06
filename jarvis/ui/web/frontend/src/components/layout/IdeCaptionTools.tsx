import { GitBranch, Mic, Plus, Search } from "lucide-react";
import { clsx } from "clsx";

import { IdeSidePanelToggle } from "@/components/agentic/sidePanel/IdeSidePanelToggle";
import { appZoomCaps } from "@/lib/appZoom";
import { useAppChordSettings } from "@/store/appChordSettings";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";

/**
 * The Agentic IDE's tools in the window caption.
 *
 * The caption is the one row that is on screen whatever the IDE shows — grid,
 * threads or the Verse — so the actions used all day live here instead of
 * behind a pane menu or the Ctrl+B key menu: a search field for every IDE
 * command, and at the right end "add an agent", voice, git and the side panel.
 *
 * Every button asks the IDE view to act through `useIdeProjectsStore`, the
 * same channel the sidebar's project tree uses, so the caption never owns IDE
 * state and stays light enough for the startup chunk.
 */

/** One shape for the icon buttons, matching the caption's back/forward buttons. */
const TOOL_BUTTON =
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground " +
  "transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground";

/**
 * The command search, left of the grid / threads / Verse switch. A button that
 * looks like a field: the palette it opens has the real one, with focus, so
 * typing starts at once.
 */
export function IdeCommandSearch({ className }: { className?: string }) {
  const openPalette = useIdeProjectsStore((state) => state.openCommandPalette);
  const chord = useAppChordSettings((state) => state.bindings.ide_commands);
  const caps = chord ? appZoomCaps(chord) : [];
  const label = caps.length ? `Search IDE commands (${caps.join("+")})` : "Search IDE commands";
  return (
    <button
      type="button"
      onClick={openPalette}
      title={label}
      aria-label={label}
      aria-haspopup="dialog"
      data-testid="ide-command-search"
      className={clsx(
        "h-6 w-56 shrink-0 items-center gap-2 rounded-md border border-border bg-secondary/60 px-2 text-xs",
        "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground dark:bg-card",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <Search aria-hidden className="h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate text-left">Search commands</span>
      {caps.length > 0 && (
        <span aria-hidden className="shrink-0 font-mono text-[10px] tracking-wide text-muted-foreground">
          {caps.join("+")}
        </span>
      )}
    </button>
  );
}

/** Add agent, voice, git and the side panel, at the caption's right end. */
export function IdeCaptionTools() {
  const workspaceId = useIdeChatStore((state) => state.workspace?.id ?? null);
  const grid = useIdeThreadsStore((state) => state.layout === "grid");
  const runCommand = useIdeProjectsStore((state) => state.runCommand);
  const toggleVoice = useIdeProjectsStore((state) => state.toggleVoice);
  const openGitPanel = useIdeProjectsStore((state) => state.openGitPanel);
  const noWorkspace = workspaceId === null;

  return (
    <div className="flex shrink-0 items-center" role="toolbar" aria-label="IDE tools" data-testid="ide-caption-tools">
      {grid && (
        <button
          type="button"
          onClick={() => runCommand({ kind: "agent-picker" })}
          disabled={noWorkspace}
          title={noWorkspace ? "Open a workspace to add a coding agent" : "Add coding agent"}
          aria-label="Add coding agent"
          data-testid="ide-caption-add-agent"
          className={TOOL_BUTTON}
        >
          <Plus aria-hidden className="h-4 w-4" />
        </button>
      )}
      <button
        type="button"
        onClick={toggleVoice}
        title="Show or hide voice"
        aria-label="Show or hide voice"
        data-testid="ide-caption-voice"
        className={TOOL_BUTTON}
      >
        <Mic aria-hidden className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => { if (workspaceId) openGitPanel(workspaceId); }}
        disabled={noWorkspace}
        title={noWorkspace ? "Open a workspace to use git" : "Git: branches, commits and worktrees"}
        aria-label="Git"
        data-testid="ide-caption-git"
        className={TOOL_BUTTON}
      >
        <GitBranch aria-hidden className="h-4 w-4" />
      </button>
      <span aria-hidden className="mx-1 h-4 w-px bg-border" />
      <IdeSidePanelToggle />
    </div>
  );
}
