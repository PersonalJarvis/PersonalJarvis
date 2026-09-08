/** Isolated visual acceptance surface. No live agents are created or modified. */
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorldStage } from "../components/society/world/WorldStage";
import { SAMPLE_ROSTER } from "../components/society/mockRoster";
import { CATALOG } from "../components/society/figures/figureRegistry";
import { useCameraStore } from "../components/society/world/cameraStore";
import { getMotionWorld } from "../components/society/world/locomotion";
import { enableWorldDiagnostics, worldDiagnostics } from "../components/society/world/worldDiagnostics";
import { buildIsland } from "../components/society/world/islandLayout";
import { ThemeProvider } from "../hooks/useTheme";
import "../index.css";

const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
const count = Math.min(30, Math.max(1, Number(new URLSearchParams(location.search).get("count")) || 30));
const agents = Array.from({ length: count }, (_, i) => ({
  ...SAMPLE_ROSTER[i % SAMPLE_ROSTER.length], agentId: `diorama-test-${i}`, name: `Agent ${i + 1}`,
  state: "idle" as const, checkpoint: "idle" as const,
  figure: { contract: 1 as const, archetype: CATALOG.bases[i % CATALOG.bases.length].archetype,
    base: CATALOG.bases[i % CATALOG.bases.length].base, parts: {} },
}));
client.setQueryDefaults(["society", "roster"], { enabled: false, refetchInterval: false });
client.setQueryData(["society", "roster"], { agents, sample: true });
enableWorldDiagnostics();
Object.assign(window, { worldLab: {
  diagnostics: worldDiagnostics,
  resetDiagnostics: enableWorldDiagnostics,
  integrity: () => {
    const world = getMotionWorld();
    const rows = [...(world?.actors.values() ?? [])];
    return { blocked: rows.filter(a => !world!.nav.free([a.x, a.z], a.radius)).map(a => a.id),
      overlaps: rows.flatMap((a, i) => rows.slice(i + 1).filter(b => Math.hypot(a.x - b.x, a.z - b.z) < a.radius + b.radius - .001).map(b => [a.id, b.id])) };
  },
  actors: () => [...(getMotionWorld()?.actors.values() ?? [])].map(a => ({ id: a.id, x: a.x, z: a.z, radius: a.radius, mode: a.mode, arrived: a.arrived })),
  camera: useCameraStore,
  island: buildIsland,
} });

createRoot(document.getElementById("root")!).render(
  <React.StrictMode><ThemeProvider><QueryClientProvider client={client}>
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={{ padding: "10px 20px", background: "var(--background)", color: "var(--foreground)" }}>
        Pixel Diorama · Development lab · {count} sample agents
      </header>
      <div style={{ flex: 1, minHeight: 0 }}><WorldStage onOpenLedger={() => undefined} /></div>
    </div>
  </QueryClientProvider></ThemeProvider></React.StrictMode>,
);
