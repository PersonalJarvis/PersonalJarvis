import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ThemeProvider } from "./hooks/useTheme";
import { ViewErrorBoundary } from "./components/ViewErrorBoundary";
import { AuthGate } from "./components/AuthGate";
import "./index.css";

// When the frontend is rebuilt while a tab is open, the old main bundle still
// references lazy chunks by their previous content hash; those URLs now 404 and
// the dynamic import() rejects, hard-crashing whichever view tried to load
// (e.g. the Wiki page's ObsidianButton). Vite fires `vite:preloadError` for
// exactly this case — reload once to pick up the fresh bundle. The
// sessionStorage guard prevents a reload loop if the chunk is missing for some
// other reason. A successful load clears the flag.
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  if (!sessionStorage.getItem("jarvis:preload-reloaded")) {
    sessionStorage.setItem("jarvis:preload-reloaded", "1");
    window.location.reload();
  }
});
window.addEventListener("load", () => {
  sessionStorage.removeItem("jarvis:preload-reloaded");
});

// Say it out loud when the main thread stops answering the user. In a WebView
// there is no console to notice it in, so a multi-second block shows up only as
// "Not responding" in the window title and as keystrokes arriving late in a
// terminal pane — with nothing anywhere to say what ran. The backend reports
// its own stalls the same way (jarvis/core/loop_watchdog.py); this covers the
// half that draws the window. Silent unless something actually blocks.
void import("./lib/uiStallWatch").then(({ watchUiStalls }) => {
  watchUiStalls((payload) => {
    void fetch("/api/diagnostics/ui-stall", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      // The thread that just unblocked may be on its way to blocking again, and
      // this report must survive that rather than be cancelled with the page.
      keepalive: true,
    }).catch(() => undefined);
  });
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ViewErrorBoundary
          viewName="App"
          resetKey="root"
          onRecover={() => window.location.reload()}
        >
          <AuthGate>
            <App />
          </AuthGate>
        </ViewErrorBoundary>
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
