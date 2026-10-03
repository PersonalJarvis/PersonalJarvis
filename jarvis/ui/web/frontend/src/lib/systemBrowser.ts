export interface BrowserBounds {
  x: number; y: number; width: number; height: number;
  viewport_width: number; viewport_height: number;
}

export interface BrowserState {
  ok?: boolean;
  available?: boolean;
  can_dock?: boolean;
  browser?: string;
  docked?: boolean;
  reason?: string;
  lease?: string;
  windows?: { id: string; title: string }[];
}

export async function systemBrowser(action: string, body?: unknown): Promise<BrowserState> {
  const response = await fetch(`/api/system-browser/${action}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(5000),
    keepalive: action === "detach",
  });
  if (!response.ok) throw new Error("Browser request failed");
  return response.json() as Promise<BrowserState>;
}

export function browserBounds(element: HTMLElement): BrowserBounds | null {
  const rect = element.getBoundingClientRect();
  if (rect.width < 500 || rect.height < 300 || rect.x < 0 || rect.y < 0) return null;
  if (rect.right > window.innerWidth + 1 || rect.bottom > window.innerHeight + 1) return null;
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height,
    viewport_width: window.innerWidth, viewport_height: window.innerHeight };
}
