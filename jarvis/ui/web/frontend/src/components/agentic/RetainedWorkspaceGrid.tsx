import { useLayoutEffect, useRef, type ComponentProps } from "react";
import { WorkspaceTerminalGrid } from "./WorkspaceTerminalGrid";

type GridProps = ComponentProps<typeof WorkspaceTerminalGrid>;

// Bound retained renderers and scrollback. A larger active workspace is always
// allowed, but then keeps no background workspace alive.
const MAX_WARM_PANES = 12;
const MAX_WARM_WORKSPACES = 3;

type Props = GridProps & {
  onScreen: boolean;
  workspaceIds?: readonly string[];
};

/** Keep recent terminal views alive instead of replaying their history on every switch. */
export function RetainedWorkspaceGrid({ onScreen, workspaceIds, ...current }: Props) {
  const retained = useRef<GridProps[]>([]);
  const activePaneIds = new Set(current.session.terminals.flatMap((pane) => pane.history_id ? [pane.history_id] : []));
  const entries = [current];
  let panes = current.session.terminals.length;
  for (const previous of retained.current) {
    if (previous.session.id === current.session.id) continue;
    if (workspaceIds && !workspaceIds.includes(previous.session.id)) continue;
    // A transferred pane belongs to its new workspace. Do not retain another
    // viewer from the source workspace's stale snapshot.
    if (previous.session.terminals.some((pane) => pane.history_id && activePaneIds.has(pane.history_id))) continue;
    if (entries.length >= MAX_WARM_WORKSPACES || panes + previous.session.terminals.length > MAX_WARM_PANES) continue;
    entries.push(previous);
    panes += previous.session.terminals.length;
  }
  useLayoutEffect(() => { retained.current = entries; });

  return <>{entries.map((entry) => {
    const visible = entry.session.id === current.session.id;
    const active = onScreen && visible;
    return <div key={entry.session.id} hidden={!visible} className="h-full min-h-0" data-workspace-terminal-view={entry.session.id}>
      <WorkspaceTerminalGrid {...entry} active={active} disabled={!active || entry.disabled} />
    </div>;
  })}</>;
}
