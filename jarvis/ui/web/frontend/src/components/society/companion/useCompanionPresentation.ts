import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useEventStore } from "@/store/events";
import { fetchMarsSnapshot, WORLD_ID, type CommandState } from "../mars/api";
import type { AssistantPresentation } from "./kinematics";

/** Ordinary-world adapter only. A swarm must supply its own scoped adapter. */
export function useCompanionPresentation(awake: boolean): AssistantPresentation {
  const voice = useEventStore((state) => state.voiceState);
  const [pollMs] = useState(() => 3000 + Math.floor(Math.random() * 1000));
  const snapshot = useQuery({
    queryKey: ["mars", WORLD_ID, "snapshot"],
    queryFn: ({ signal }) => fetchMarsSnapshot(signal),
    enabled: awake, refetchInterval: awake ? pollMs : false, retry: false,
  });
  const previous = useRef<Map<string, CommandState> | null>(null);
  const [terminal, setTerminal] = useState<AssistantPresentation["task"]>("idle");
  useEffect(() => {
    const commands = snapshot.data?.commands;
    if (!commands) return;
    let changed: AssistantPresentation["task"] = "idle";
    for (const command of commands) {
      // Initial history is a baseline. Reopening the world never celebrates old work.
      if (!previous.current?.has(command.command_id) || previous.current.get(command.command_id) === command.state) continue;
      if (command.state === "failed") changed = "error";
      else if (command.state === "interrupted" || command.state === "unknown") changed = "blocked";
      else if (command.state === "completed" && changed === "idle") changed = "complete";
    }
    previous.current = new Map(commands.map((command) => [command.command_id, command.state]));
    if (changed !== "idle") setTerminal(changed);
  }, [snapshot.data]);
  useEffect(() => {
    if (terminal === "idle") return;
    const timeout = setTimeout(() => setTerminal("idle"), 5000);
    return () => clearTimeout(timeout);
  }, [terminal]);
  const active = snapshot.data?.commands.some((command) => command.state === "active" || command.state === "queued") ?? false;
  return {
    audio: voice === "speaking" || voice === "listening" ? voice : "idle",
    task: voice === "error" ? "error" : active || voice === "thinking" ? "working" : terminal,
    muted: voice === "paused",
  };
}
