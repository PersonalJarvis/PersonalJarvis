/**
 * Setup-related types shared between frontend components.
 *
 * Mirrors the response shape of `GET /api/setup/obsidian/status` from
 * `jarvis/ui/web/setup_routes.py::ObsidianStatusResponse`. If the backend
 * model gains a field, mirror it here — TypeScript strict mode will flag
 * any consumer that has not been updated.
 */

export type ObsidianRecommendedAction =
  | "ok"
  | "install_obsidian"
  | "register_vault";

export interface ObsidianStatus {
  installed: boolean;
  version: string | null;
  config_exists: boolean;
  vault_registered: boolean;
  vault_path: string;
  recommended_action: ObsidianRecommendedAction;
  note?: string | null;
}
