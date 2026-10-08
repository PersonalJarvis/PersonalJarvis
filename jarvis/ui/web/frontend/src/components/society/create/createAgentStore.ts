/**
 * The "new agent" dialog, opened from every plus in the society (the Agents
 * page, the office's reception, the foundry). `request()` opens it and
 * settles once the person created an agent — or rejects with
 * `CreateAgentCancelled` when they closed it — so each plus keeps its own
 * "then open the new agent's chat" step.
 */
import { create } from "zustand";
import type { SocietyAgent } from "../data";

export class CreateAgentCancelled extends Error {
  constructor() {
    super("Agent creation cancelled");
    this.name = "CreateAgentCancelled";
  }
}

interface CreateAgentDialogState {
  open: boolean;
  request: () => Promise<SocietyAgent>;
  /** The dialog created the agent: settle the pending request. */
  finish: (agent: SocietyAgent) => void;
  cancel: () => void;
}

let pending: { resolve: (agent: SocietyAgent) => void; reject: (error: Error) => void } | null = null;

export const useCreateAgentDialog = create<CreateAgentDialogState>((set) => ({
  open: false,
  request: () => {
    // A second plus while the dialog is open re-targets the same dialog.
    pending?.reject(new CreateAgentCancelled());
    return new Promise<SocietyAgent>((resolve, reject) => {
      pending = { resolve, reject };
      set({ open: true });
    });
  },
  finish: (agent) => {
    pending?.resolve(agent);
    pending = null;
    set({ open: false });
  },
  cancel: () => {
    pending?.reject(new CreateAgentCancelled());
    pending = null;
    set({ open: false });
  },
}));

export function isCreateCancelled(error: unknown): boolean {
  return error instanceof CreateAgentCancelled;
}
