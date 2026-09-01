/**
 * The one way a person is drawn in this product.
 *
 * Wraps `lib/identityAvatar` so Profile, Contacts, Friends and the review
 * queue cannot drift into four different discs. Three sizes, one shape (pill),
 * no border: the disc has a real fill, and Design.md's separation rule is fill
 * first — a rim on top of a solid colour is the wireframe look it warns about.
 *
 * `src` renders a real portrait instead of the initials; the coloured disc
 * stays underneath as the ground, so a slow or broken image never flashes an
 * empty grey hole.
 */
import { cn } from "@/lib/utils";
import { identityAvatarStyle, identityInitials } from "@/lib/identityAvatar";

/** Disc diameter and the glyph size that belongs to it. */
const SIZES = {
  sm: "h-7 w-7 text-micro",
  md: "h-9 w-9 text-meta",
  lg: "h-14 w-14 text-page",
} as const;

export type IdentityAvatarSize = keyof typeof SIZES;

export function IdentityAvatar({
  name,
  size = "md",
  src,
  alt,
  className,
}: {
  name: string;
  size?: IdentityAvatarSize;
  /** A real portrait, drawn on top of the identity disc. */
  src?: string | null;
  alt?: string;
  className?: string;
}) {
  return (
    <span
      aria-hidden={src ? undefined : true}
      style={identityAvatarStyle(name)}
      className={cn(
        "relative flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold",
        SIZES[size],
        className,
      )}
    >
      {src ? (
        <img src={src} alt={alt ?? name} className="h-full w-full object-cover" draggable={false} />
      ) : (
        identityInitials(name)
      )}
    </span>
  );
}
