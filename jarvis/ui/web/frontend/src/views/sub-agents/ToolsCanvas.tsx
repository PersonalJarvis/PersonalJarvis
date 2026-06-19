/**
 * ToolsCanvas — Tools eines einzelnen Sub-Agents als Icon-Canvas.
 *
 * Ansicht wird aktiv, wenn der User einen Sub-Agent-Knoten in der
 * SubAgentsView anklickt. Jeder Tool-Call wird als Icon-Node gerendert,
 * Status via Rahmenfarbe (sky=running, emerald=completed, red=failed).
 * Klick auf einen Tool-Knoten klappt einen Output-Preview unten auf.
 */
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, Brain, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import type { SubAgentNode, ToolCallEntry } from "@/store/subAgents";

import { getToolAppearance, type ToolAppearance } from "./tool-icons";

const TOOL_NODE_WIDTH = 160;
const TOOL_NODE_HEIGHT = 120;
const H_SPACING = 40;
const V_SPACING = 40;

interface ToolNodeData extends Record<string, unknown> {
  entry: ToolCallEntry;
  appearance: ToolAppearance;
  index: number;
}

type ToolNode = Node<ToolNodeData>;

// ── Tool-Card ──────────────────────────────────────────────────────

function ToolCard({ data }: NodeProps<Node<ToolNodeData>>) {
  const { entry, appearance } = data;
  const { Icon } = appearance;

  const statusRing =
    entry.status === "running"
      ? "ring-sky-400 ring-2 animate-pulse"
      : entry.status === "completed"
        ? "ring-emerald-400 ring-2"
        : entry.status === "failed"
          ? "ring-red-400 ring-2"
          : "ring-zinc-600 ring-1";

  return (
    <motion.div
      initial={{ scale: 0.7, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      exit={{ scale: 0.7, opacity: 0 }}
      transition={{ type: "spring", stiffness: 300, damping: 24 }}
      className={`w-[${TOOL_NODE_WIDTH}px] rounded-2xl ${appearance.bg} ${statusRing} p-3 shadow-lg backdrop-blur-sm flex flex-col items-center gap-2`}
      style={{ width: TOOL_NODE_WIDTH }}
    >
      <div
        className={`w-14 h-14 rounded-full ${appearance.bg} border border-zinc-700 flex items-center justify-center shadow-inner`}
      >
        <Icon className={`h-7 w-7 ${appearance.iconColor}`} />
      </div>
      <div className="text-center min-w-0 w-full">
        <div className="text-xs font-semibold text-zinc-100 truncate">
          {appearance.label}
        </div>
        <div className="text-[10px] text-zinc-400 truncate">
          {entry.tool_name}
        </div>
        {entry.duration_ms !== undefined && entry.duration_ms > 0 && (
          <div className="text-[10px] text-zinc-500 mt-0.5">
            {(entry.duration_ms / 1000).toFixed(1)}s
          </div>
        )}
      </div>
    </motion.div>
  );
}

const nodeTypes = { tool: ToolCard };

// ── Haupt-Canvas ───────────────────────────────────────────────────

export function ToolsCanvas({
  agent,
  onBack,
}: {
  agent: SubAgentNode;
  onBack: () => void;
}) {
  const toolCalls = agent.tool_calls;

  // Layout: Tools in einem Grid, bis zu 4 pro Zeile.
  const initialNodes = useMemo<ToolNode[]>(() => {
    const nodes: ToolNode[] = [];
    const perRow = 4;
    toolCalls.forEach((tc, i) => {
      const row = Math.floor(i / perRow);
      const col = i % perRow;
      nodes.push({
        id: tc.trace_id || `tool-${i}`,
        type: "tool",
        position: {
          x: col * (TOOL_NODE_WIDTH + H_SPACING),
          y: row * (TOOL_NODE_HEIGHT + V_SPACING),
        },
        data: {
          entry: tc,
          appearance: getToolAppearance(tc.tool_name, tc.args_preview),
          index: i,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
      });
    });
    return nodes;
  }, [toolCalls]);

  const initialEdges = useMemo<Edge[]>(() => {
    // Chrono-Pfeile: Tool-Call n → Tool-Call n+1 (innerhalb einer Reihe).
    const edges: Edge[] = [];
    const perRow = 4;
    for (let i = 0; i < toolCalls.length - 1; i++) {
      const rowI = Math.floor(i / perRow);
      const rowJ = Math.floor((i + 1) / perRow);
      if (rowI !== rowJ) continue; // Kein Pfeil zwischen Reihen
      edges.push({
        id: `e-${i}-${i + 1}`,
        source: toolCalls[i].trace_id || `tool-${i}`,
        target: toolCalls[i + 1].trace_id || `tool-${i + 1}`,
        animated: toolCalls[i + 1].status === "running",
        style: { stroke: "#52525b", strokeWidth: 1.5 },
      });
    }
    return edges;
  }, [toolCalls]);

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<ToolNode>(initialNodes);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  useEffect(() => {
    setRfNodes(initialNodes);
  }, [initialNodes, setRfNodes]);

  useEffect(() => {
    setRfEdges(initialEdges);
  }, [initialEdges, setRfEdges]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      const match = rfNodes.find((n) => n.id === node.id);
      if (match) setSelectedIdx(match.data.index);
    },
    [rfNodes],
  );

  const selectedTool = selectedIdx !== null ? toolCalls[selectedIdx] : null;
  const selectedAppearance = selectedTool
    ? getToolAppearance(selectedTool.tool_name, selectedTool.args_preview)
    : null;

  const runningCount = toolCalls.filter((tc) => tc.status === "running").length;

  return (
    <div className="relative h-full w-full flex flex-col bg-zinc-950">
      {/* Header ────────────────────────────────────────────────── */}
      <header className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between shrink-0 z-10 bg-zinc-950/80 backdrop-blur">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onBack}
            className="text-zinc-400 hover:text-zinc-100 p-1.5 rounded hover:bg-zinc-800 flex items-center gap-1.5 text-xs"
            title="Zurück zur Agents-Übersicht"
          >
            <ArrowLeft className="h-4 w-4" />
            Zurück
          </button>
          <div className="h-6 w-px bg-zinc-800" />
          <div className="flex items-center gap-2 min-w-0">
            <Brain className="h-4 w-4 text-violet-400 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-semibold text-zinc-100 truncate">
                {agent.name}
              </div>
              <div className="text-[11px] text-zinc-500 truncate">
                {toolCalls.length} Tool-Calls · {runningCount} aktiv
                {agent.utterance && <> · „{agent.utterance}"</>}
              </div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-zinc-400 shrink-0">
          {agent.tokens_out > 0 && <span>{agent.tokens_out} tok</span>}
          {agent.cost_usd > 0 && <span>${agent.cost_usd.toFixed(4)}</span>}
        </div>
      </header>

      {/* Canvas ────────────────────────────────────────────────── */}
      <div className="flex-1 relative">
        {toolCalls.length === 0 ? (
          <EmptyTools agentName={agent.name} />
        ) : (
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.25 }}
            proOptions={{ hideAttribution: true }}
            className="bg-zinc-950"
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={20}
              size={1}
              color="#27272a"
            />
            <Controls className="!bg-zinc-900 !border-zinc-800" />
            <MiniMap
              className="!bg-zinc-900 !border-zinc-800"
              nodeColor={() => "#52525b"}
              maskColor="rgba(0,0,0,0.6)"
            />
          </ReactFlow>
        )}

        {/* Detail-Sheet unten wenn Tool angeklickt */}
        <AnimatePresence>
          {selectedTool && selectedAppearance && (
            <ToolDetailSheet
              tool={selectedTool}
              appearance={selectedAppearance}
              onClose={() => setSelectedIdx(null)}
            />
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

// ── Bottom-Sheet fuer Tool-Details ─────────────────────────────────

function ToolDetailSheet({
  tool,
  appearance,
  onClose,
}: {
  tool: ToolCallEntry;
  appearance: ToolAppearance;
  onClose: () => void;
}) {
  const { Icon } = appearance;
  return (
    <motion.div
      initial={{ y: 300, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      exit={{ y: 300, opacity: 0 }}
      transition={{ type: "spring", stiffness: 250, damping: 28 }}
      className="absolute bottom-4 left-4 right-4 z-20 max-h-[45%] rounded-2xl bg-zinc-900/95 border border-zinc-800 backdrop-blur-md shadow-2xl flex flex-col"
    >
      <header className="px-4 py-3 border-b border-zinc-800 flex items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div
            className={`w-9 h-9 rounded-full ${appearance.bg} border border-zinc-700 flex items-center justify-center`}
          >
            <Icon className={`h-4 w-4 ${appearance.iconColor}`} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-zinc-100 truncate">
              {appearance.label}
            </div>
            <div className="text-[11px] text-zinc-500 font-mono truncate">
              {tool.tool_name}
            </div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-400 hover:text-zinc-100 text-xs px-2 py-1 rounded hover:bg-zinc-800"
        >
          schließen
        </button>
      </header>

      <ScrollArea className="flex-1">
        <div className="p-4 text-xs text-zinc-300 space-y-3">
          <div className="flex flex-wrap gap-3">
            <KV label="Status" value={tool.status} />
            {tool.duration_ms !== undefined && (
              <KV label="Dauer" value={`${(tool.duration_ms / 1000).toFixed(2)}s`} />
            )}
          </div>

          <div>
            <div className="text-[10px] uppercase text-zinc-500 mb-1">
              Arguments
            </div>
            <pre className="rounded bg-zinc-800/60 p-2 text-[11px] whitespace-pre-wrap text-zinc-200 break-all">
              {tool.args_preview || "(leer)"}
            </pre>
          </div>

          {tool.output_preview && (
            <div>
              <div className="text-[10px] uppercase text-zinc-500 mb-1">
                Output
              </div>
              <pre className="rounded bg-zinc-800/60 p-2 text-[11px] whitespace-pre-wrap text-zinc-200 break-all">
                {tool.output_preview}
              </pre>
            </div>
          )}

          {tool.error && (
            <div className="rounded bg-red-900/30 border border-red-700/50 p-2 text-red-200">
              {tool.error}
            </div>
          )}
        </div>
      </ScrollArea>
    </motion.div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-zinc-500 text-[10px] uppercase">{label}</span>
      <span className="text-zinc-200 font-mono">{value}</span>
    </div>
  );
}

function EmptyTools({ agentName }: { agentName: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-8 gap-3">
      <div className="rounded-full bg-zinc-900 border border-zinc-800 p-4">
        <Sparkles className="h-8 w-8 text-violet-400" />
      </div>
      <h2 className="text-base font-medium text-zinc-100">
        {agentName} hat noch keine Tools aufgerufen.
      </h2>
      <p className="max-w-md text-sm text-zinc-500">
        Sobald der OpenClaw-Worker ein Tool triggert (z.&nbsp;B. dispatch_to_harness,
        run_shell, screenshot), erscheint es hier als Icon-Node.
      </p>
    </div>
  );
}
