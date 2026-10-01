import { create } from "zustand";

import type { PermissionId } from "@/hooks/usePermissions";

/**
 * The one just-in-time macOS permission card.
 *
 * A feature the user just started asks for the access it needs, at that
 * moment, and only for that access — see `PermissionPrompt`. The request
 * comes from the backend (`PermissionNeeded` on the event stream) or from a
 * place in the UI that is about to need a grant (turning the wake word on).
 */
export interface PermissionRequest {
  permission: PermissionId;
  /** A `FEATURE_REQUIREMENTS` key; picks the "why" sentence. */
  feature: string;
}

interface PermissionPromptState {
  request: PermissionRequest | null;
  ask: (request: PermissionRequest) => void;
  dismiss: () => void;
}

export const usePermissionPrompt = create<PermissionPromptState>((set, get) => ({
  request: null,
  // One card at a time: a second request while one is open is the same
  // conversation, not a new one.
  ask: (request) => {
    if (get().request === null) set({ request });
  },
  dismiss: () => set({ request: null }),
}));

/** Ask for a grant from code outside React (hooks' callbacks, event handlers). */
export function askPermission(permission: PermissionId, feature: string): void {
  usePermissionPrompt.getState().ask({ permission, feature });
}
