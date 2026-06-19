import { IntroSequence } from "./IntroSequence";

export function IntroClip({ src }: { src?: string }) {
  if (src && src.trim().length > 0) {
    return (
      <video
        className="aspect-video w-full rounded-xl border border-border"
        src={src}
        controls
        playsInline
        preload="metadata"
      />
    );
  }
  return <IntroSequence />;
}
