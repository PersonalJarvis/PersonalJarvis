import type { SectionId } from "@/store/events";

type Loader = () => Promise<unknown>;

/** Register without executing: unopened windows must not warm every feature. */
export function createSectionPrefetcher() {
  const loaders = new Map<SectionId, Loader>();
  const pending = new Map<Loader, Promise<void>>();

  return {
    register(sections: readonly SectionId[], loader: Loader) {
      for (const section of sections) loaders.set(section, loader);
    },
    prefetch(section: SectionId): Promise<void> {
      const loader = loaders.get(section);
      if (!loader) return Promise.resolve();
      const existing = pending.get(loader);
      if (existing) return existing;
      const request = Promise.resolve().then(loader).then(
        () => undefined,
        () => {
          // Speculation is optional. Navigation retries through React.lazy
          // and its error boundary; a failed warm-up must not poison it.
          pending.delete(loader);
        },
      );
      pending.set(loader, request);
      return request;
    },
  };
}

export const sectionPrefetch = createSectionPrefetcher();
