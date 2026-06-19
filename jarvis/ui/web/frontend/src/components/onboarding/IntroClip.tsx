import { MascotGigi } from "@/components/MascotGigi";

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
  return (
    <div className="flex aspect-video w-full items-center justify-center rounded-xl border border-border bg-gradient-to-br from-background to-card">
      <MascotGigi size={120} reactToVoice={false} enableComments={false} />
    </div>
  );
}
