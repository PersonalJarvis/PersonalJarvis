/**
 * Talking to `/api/marketplace/connectors` — a remote MCP server added by URL.
 *
 * One call: the backend asks the server how it signs in, and only writes the
 * catalog entry when the answer is usable. The reply carries that entry, so
 * the caller can start the matching connect flow straight away.
 */

export type ConnectorSignIn = "auto" | "oauth" | "token" | "none";

export type CustomConnectorRequest = {
  name: string;
  url: string;
  auth: ConnectorSignIn;
  /** Header an access token travels in; empty means Authorization (Bearer). */
  headerName?: string;
};

export type CustomConnectorResult = {
  ok: boolean;
  plugin: { id: string; display_name: string; source: string };
  detected: {
    auth: Exclude<ConnectorSignIn, "auto">;
    transport: "http" | "sse";
    server_name: string | null;
  };
};

export async function addCustomConnector(
  request: CustomConnectorRequest,
): Promise<CustomConnectorResult> {
  const res = await fetch("/api/marketplace/connectors", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: request.name.trim(),
      url: request.url.trim(),
      auth: request.auth,
      header_name: request.headerName?.trim() || null,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    // FastAPI validation errors arrive as a list; show the first message.
    if (Array.isArray(detail) && detail[0]?.msg) throw new Error(String(detail[0].msg));
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return res.json();
}

/** Removes a plugin the owner added (connector, upload or community install). */
export async function removeAddedPlugin(pluginId: string): Promise<void> {
  const res = await fetch(`/api/marketplace/community/plugins/${encodeURIComponent(pluginId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
}

/**
 * A plugin another surface wants opened next time the Plugins view mounts —
 * the composer's Add menu uses it so "Connect" lands on that plugin's page
 * instead of the top of a long list.
 */
let pendingFocus: string | null = null;

export function requestPluginFocus(pluginId: string): void {
  pendingFocus = pluginId;
}

export function takePluginFocus(): string | null {
  const id = pendingFocus;
  pendingFocus = null;
  return id;
}
