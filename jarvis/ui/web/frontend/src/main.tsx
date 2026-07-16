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
