/**
 * The agent's REAL chat on its desk monitor: the same AgentChatPanel the chat
 * view shows, live, drawn onto the screen plane. Each monitor owns its own
 * chat store (one socket through the shared connect budget), so it never
 * touches the chat the rest of the app has open. Display only: clicks go to
 * the monitor underneath, which dives into the chat view.
 */
import { Suspense, lazy, useEffect, useMemo } from "react";
import { Html } from "@react-three/drei";
import { QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { createAgentChatStore } from "@/store/agentChat";
import type { SocietyAgent } from "../data";
import { useBlendingCanvasLayer } from "./blendingLayer";

const AgentChatPanel = lazy(() => import("../chat/AgentChatPanel").then((m) => ({ default: m.AgentChatPanel })));

/** The chat is laid out at a normal window size, then scaled onto the 0.66 × 0.38 m screen. */
export const REAL_CHAT_PX = { w: 880, h: 507 } as const;
const SCREEN_W_M = 0.66;
/** drei maps one CSS pixel to distanceFactor / 400 metres in transform mode. */
const SCREEN_Z_RANGE: [number, number] = [10, 0];
const DISTANCE_FACTOR = (SCREEN_W_M * 400) / REAL_CHAT_PX.w;

export function RealChatScreen({ agent, roster, position }: {
  agent: SocietyAgent; roster: SocietyAgent[]; position: [number, number, number];
}) {
  // drei renders <Html> content in a separate React root: the app's data client
  // must be handed over, or the chat panel's queries throw and the screen stays blank.
  const client = useQueryClient();
  const store = useMemo(() => createAgentChatStore("society", `office-monitor:${agent.agentId}`), [agent.agentId]);
  // Leaving the monitor closes its socket; the chat itself keeps running on the server.
  useEffect(() => () => store.getState().disconnect(), [store]);
  // Another <Html> mounting later would drop the canvas under this screen (see blendingLayer).
  useBlendingCanvasLayer(SCREEN_Z_RANGE);
  return (
    <Html transform occlude="blending" position={position} distanceFactor={DISTANCE_FACTOR} zIndexRange={SCREEN_Z_RANGE}
      style={{ width: REAL_CHAT_PX.w, height: REAL_CHAT_PX.h, pointerEvents: "none" }}>
      <div className="office-real-chat" data-office-ui>
        <QueryClientProvider client={client}>
          <Suspense fallback={null}>
            <AgentChatPanel agent={agent} roster={roster} chatStore={store} />
          </Suspense>
        </QueryClientProvider>
      </div>
    </Html>
  );
}
