"""The one table of macOS ``Info.plist`` usage-description strings.

Standard-library-only, no ``jarvis`` imports, no I/O and no work at import time.
Two callers cannot import the package normally and therefore load THIS FILE BY
PATH (``importlib.util.spec_from_file_location``), so neither carries a private
copy of the strings:

* ``jarvis.spec`` - PyInstaller evaluates the spec from the checkout before the
  package is importable, and the strings end up in the downloadable ``.dmg``
  app's ``Info.plist``.
* ``jarvis.setup.macos_app_bundle._bundle_plist`` - the managed source-install
  app's ``Info.plist`` (a source checkout, so the file is on disk).

``tests/unit/packaging/test_macos_privacy_strings.py`` loads the same file and
asserts that the spec's plist dict, the managed bundle's plist and this table
agree, and ``scripts/ci/check_frozen_macos_app.py`` asserts the table on the
built ``.app``. Keep this module loadable by path: constants and plain
functions only (a ``dataclass`` or ``enum`` here would need the module to be
registered in ``sys.modules`` first).

What the strings are for. macOS shows the string of the RESPONSIBLE app in the
system dialog, so each one must say, in one plain sentence, which feature the
person is about to use. The wording follows Apple's guidance for purpose
strings (a brief, complete sentence, specific, in the active voice, ending in a
period). Personal Jarvis asks only when a feature needs a permission, never at
launch, so every sentence names that feature.

Why some keys are deliberately absent (least privilege; nothing is requested
"just in case"): Jarvis has no caller for the camera, for Apple's speech
recognition service or for system-administration APIs, so a string for them
would promise something the app never does. :data:`REMOVED_USAGE_KEYS` lists
those keys so a build that brings one back is caught by CI. Accessibility,
Input Monitoring and posting synthetic events have no usage-description key at
all (their dialog text is fixed by macOS). Services only a child process could
reach (Contacts, Calendars, Photos, Location, Bluetooth) get no string either:
an agent feature that needs one must add it here together with its caller.
"""

from __future__ import annotations

# Must equal ``jarvis.core.branding.PRODUCT_NAME``; a parity test pins it
# (this file may not import the package).
PRODUCT_NAME = "Personal Jarvis"

_USAGE_DESCRIPTIONS: dict[str, str] = {
    "NSMicrophoneUsageDescription": (
        f"{PRODUCT_NAME} uses the microphone when you dictate, talk to it, "
        "or switch on the wake word."
    ),
    # Kept for parity between the two bundles. Apple's documentation index has
    # no page for this key, so its effect on macOS is UNVERIFIED; Screen
    # Recording is granted in System Settings whether or not the key exists.
    "NSScreenCaptureUsageDescription": (
        f"{PRODUCT_NAME} captures the screen when you ask it to look at what "
        "is on screen or to control an app for you."
    ),
    # Targets today: Music and Spotify (muting music while you dictate) and
    # Terminal (sign-in and coding-agent windows opened through AppleScript).
    "NSAppleEventsUsageDescription": (
        f"{PRODUCT_NAME} sends commands to other apps when a feature you use "
        "needs it, such as lowering Music or Spotify while you dictate."
    ),
    "NSDesktopFolderUsageDescription": (
        f"{PRODUCT_NAME} opens or saves files in your Desktop folder when you "
        "ask it to work with them."
    ),
    "NSDocumentsFolderUsageDescription": (
        f"{PRODUCT_NAME} opens or saves files in your Documents folder when "
        "you ask it to work with them."
    ),
    "NSDownloadsFolderUsageDescription": (
        f"{PRODUCT_NAME} saves and opens files in your Downloads folder when "
        "you ask it to download or work with them."
    ),
    "NSRemovableVolumesUsageDescription": (
        f"{PRODUCT_NAME} opens or saves files on an external drive when you "
        "ask it to work with them."
    ),
    "NSNetworkVolumesUsageDescription": (
        f"{PRODUCT_NAME} opens or saves files on a network drive when you ask it to work with them."
    ),
    # The app itself serves only 127.0.0.1; the local-network prompt is about
    # the devices and hosts the person configures (a smart-home hub, a local AI
    # server), which is the one real LAN use of this key.
    "NSLocalNetworkUsageDescription": (
        f"{PRODUCT_NAME} connects to devices and hosts on your local network "
        "that you set up, such as a smart-home hub or a local AI server."
    ),
}

# Every key a built bundle must carry, in table order.
REQUIRED_USAGE_KEYS: tuple[str, ...] = tuple(_USAGE_DESCRIPTIONS)

# Keys that must NOT appear in any bundle: no caller exists, so the string (and,
# for the camera, the matching entitlement) would claim access Jarvis never uses.
REMOVED_USAGE_KEYS: tuple[str, ...] = (
    "NSCameraUsageDescription",
    "NSSpeechRecognitionUsageDescription",
    "NSSystemAdministrationUsageDescription",
)


def usage_descriptions() -> dict[str, str]:
    """A fresh ``{Info.plist key: usage string}`` dict, safe for the caller to mutate."""
    return dict(_USAGE_DESCRIPTIONS)
