/**
 * Contacts' view of the shared identity mark.
 *
 * The implementation moved to `@/lib/identityAvatar` when the coloured avatar
 * became the app-wide rule (Design.md: colour has three jobs — life, fault,
 * identity) rather than a Contacts-only flourish. These aliases stay so the
 * Contacts call sites keep reading in their own vocabulary; there is exactly
 * one hash, so a person is the same colour everywhere.
 */
export {
  identityAvatarStyle as contactAvatarStyle,
  identityInitials as contactInitials,
} from "@/lib/identityAvatar";
