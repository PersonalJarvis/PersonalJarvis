"""The one table of macOS ``Info.plist`` usage-description strings.

Standard-library-only, no ``jarvis`` imports and no work at import time (the one
file-writing function, :func:`write_localizations`, runs only when a build calls it).
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

from pathlib import Path

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
    # Target-neutral on purpose: macOS names the app being controlled in the
    # dialog itself ("... wants access to control Music"), and the targets today
    # are Music, Spotify (lowering music while you dictate) and Terminal (sign-in
    # and coding-agent windows opened through AppleScript). A sentence naming one
    # of them would be wrong in the other two dialogs.
    "NSAppleEventsUsageDescription": (
        f"{PRODUCT_NAME} sends commands to other apps, such as a music player "
        "or a terminal, when a feature you use needs it."
    ),
    # The folder sentences stay short: the system dialog already names the folder.
    "NSDesktopFolderUsageDescription": (
        f"{PRODUCT_NAME} works with files in your Desktop folder when you ask it to."
    ),
    "NSDocumentsFolderUsageDescription": (
        f"{PRODUCT_NAME} works with files in your Documents folder when you ask it to."
    ),
    "NSDownloadsFolderUsageDescription": (
        f"{PRODUCT_NAME} works with files in your Downloads folder when you ask it to."
    ),
    "NSRemovableVolumesUsageDescription": (
        f"{PRODUCT_NAME} works with files on an external drive when you ask it to."
    ),
    "NSNetworkVolumesUsageDescription": (
        f"{PRODUCT_NAME} works with files on a network drive when you ask it to."
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


# --- Localisation (German, Spanish and European Portuguese) ------------------
#
# macOS shows the usage string of the user's preferred language when the bundle
# is localised for it: ``Contents/Resources/<lang>.lproj/InfoPlist.strings``
# overrides the base ``Info.plist`` value, and ``CFBundleLocalizations`` lists the
# languages the bundle carries. English stays the base text in ``Info.plist``
# itself (and the development region), so a language without a table falls back to
# it. The two bundles share this one table: ``jarvis.spec`` lists the languages in
# its plist and ``packaging/macos/add_localizations.py`` writes the files into the
# built ``.dmg`` app before it is signed; the managed bundle does both itself.
# The wording follows the English table and the in-app German/Spanish/Portuguese
# copy ("du" / "tu"). German, Spanish and Portuguese are allowed here because this
# is the closed product surface (``scripts/ci/german-allowlist.txt``). That macOS
# shows these strings in the permission dialog of a localised system is UNVERIFIED
# (no Mac was available).

#: Languages the bundles ship an Info.plist for; ``en`` is the base text.
LOCALIZATIONS: tuple[str, ...] = ("en", "de", "es", "pt-PT")

#: ``CFBundleDevelopmentRegion``: the language of the base ``Info.plist`` strings.
DEVELOPMENT_REGION = "en"

_LOCALIZED_USAGE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "de": {
        "NSMicrophoneUsageDescription": (
            f"{PRODUCT_NAME} verwendet das Mikrofon, wenn du diktierst, mit Jarvis "
            "sprichst oder das Wake-Word einschaltest."
        ),
        "NSScreenCaptureUsageDescription": (
            f"{PRODUCT_NAME} erfasst den Bildschirm, wenn du Jarvis bittest, ihn "
            "anzusehen oder eine App für dich zu steuern."
        ),
        "NSAppleEventsUsageDescription": (
            f"{PRODUCT_NAME} sendet Befehle an andere Apps, etwa an einen "
            "Musikplayer oder eine Konsole, wenn eine Funktion, die du nutzt, "
            "es braucht."
        ),
        "NSDesktopFolderUsageDescription": (
            f"{PRODUCT_NAME} arbeitet mit Dateien in deinem Schreibtischordner, "
            "wenn du darum bittest."
        ),
        "NSDocumentsFolderUsageDescription": (
            f"{PRODUCT_NAME} arbeitet mit Dateien in deinem Dokumentenordner, "
            "wenn du darum bittest."
        ),
        "NSDownloadsFolderUsageDescription": (
            f"{PRODUCT_NAME} arbeitet mit Dateien in deinem Downloads-Ordner, "
            "wenn du darum bittest."
        ),
        "NSRemovableVolumesUsageDescription": (
            f"{PRODUCT_NAME} arbeitet mit Dateien auf einem externen Laufwerk, "
            "wenn du darum bittest."
        ),
        "NSNetworkVolumesUsageDescription": (
            f"{PRODUCT_NAME} arbeitet mit Dateien auf einem Netzwerklaufwerk, "
            "wenn du darum bittest."
        ),
        "NSLocalNetworkUsageDescription": (
            f"{PRODUCT_NAME} verbindet sich mit Geräten und Hosts in deinem "
            "lokalen Netzwerk, die du eingerichtet hast, etwa einem "
            "Smart-Home-Hub oder einem lokalen KI-Server."
        ),
    },
    "es": {
        "NSMicrophoneUsageDescription": (
            f"{PRODUCT_NAME} usa el micrófono cuando dictas, hablas con Jarvis "
            "o activas la palabra de activación."
        ),
        "NSScreenCaptureUsageDescription": (
            f"{PRODUCT_NAME} captura la pantalla cuando le pides que mire lo "
            "que hay en ella o que controle una app por ti."
        ),
        "NSAppleEventsUsageDescription": (
            f"{PRODUCT_NAME} envía comandos a otras apps, como un reproductor "
            "de música o una terminal, cuando una función que usas lo necesita."
        ),
        "NSDesktopFolderUsageDescription": (
            f"{PRODUCT_NAME} trabaja con archivos de tu carpeta Escritorio cuando se lo pides."
        ),
        "NSDocumentsFolderUsageDescription": (
            f"{PRODUCT_NAME} trabaja con archivos de tu carpeta Documentos cuando se lo pides."
        ),
        "NSDownloadsFolderUsageDescription": (
            f"{PRODUCT_NAME} trabaja con archivos de tu carpeta Descargas cuando se lo pides."
        ),
        "NSRemovableVolumesUsageDescription": (
            f"{PRODUCT_NAME} trabaja con archivos de una unidad externa cuando se lo pides."
        ),
        "NSNetworkVolumesUsageDescription": (
            f"{PRODUCT_NAME} trabaja con archivos de una unidad de red cuando se lo pides."
        ),
        "NSLocalNetworkUsageDescription": (
            f"{PRODUCT_NAME} se conecta con dispositivos y equipos de tu red "
            "local que hayas configurado, como un hub doméstico inteligente o "
            "un servidor de IA local."
        ),
    },
    # European Portuguese. Apple names the bundle localisation ``pt-PT``
    # (``pt-PT.lproj``); a bare ``pt`` would be read as Brazilian Portuguese.
    "pt-PT": {
        "NSMicrophoneUsageDescription": (
            f"{PRODUCT_NAME} usa o microfone quando ditas, falas com o Jarvis "
            "ou ativas a palavra de ativação."
        ),
        "NSScreenCaptureUsageDescription": (
            f"{PRODUCT_NAME} captura o ecrã quando lhe pedes para ver o que lá "
            "está ou para controlar uma app por ti."
        ),
        "NSAppleEventsUsageDescription": (
            f"{PRODUCT_NAME} envia comandos a outras apps, como um leitor de "
            "música ou um terminal, quando uma funcionalidade que usas precisa."
        ),
        "NSDesktopFolderUsageDescription": (
            f"{PRODUCT_NAME} trabalha com ficheiros da tua pasta Secretária quando lho pedes."
        ),
        "NSDocumentsFolderUsageDescription": (
            f"{PRODUCT_NAME} trabalha com ficheiros da tua pasta Documentos quando lho pedes."
        ),
        "NSDownloadsFolderUsageDescription": (
            f"{PRODUCT_NAME} trabalha com ficheiros da tua pasta Transferências quando lho pedes."
        ),
        "NSRemovableVolumesUsageDescription": (
            f"{PRODUCT_NAME} trabalha com ficheiros de um disco externo quando lho pedes."
        ),
        "NSNetworkVolumesUsageDescription": (
            f"{PRODUCT_NAME} trabalha com ficheiros de um disco de rede quando lho pedes."
        ),
        "NSLocalNetworkUsageDescription": (
            f"{PRODUCT_NAME} liga-se a dispositivos e servidores da tua rede "
            "local que configuraste, como um hub de casa inteligente ou um "
            "servidor de IA local."
        ),
    },
}

#: The languages that get an ``<lang>.lproj/InfoPlist.strings`` (English is the base).
LOCALIZED_LANGUAGES: tuple[str, ...] = tuple(_LOCALIZED_USAGE_DESCRIPTIONS)


def localized_usage_descriptions(language: str) -> dict[str, str]:
    """A fresh ``{Info.plist key: usage string}`` dict for ``language`` (e.g. ``de``, ``pt-PT``)."""
    return dict(_LOCALIZED_USAGE_DESCRIPTIONS[language])


def localization_plist_keys() -> dict[str, object]:
    """The two ``Info.plist`` keys that declare the localisations (a fresh dict)."""
    return {
        "CFBundleDevelopmentRegion": DEVELOPMENT_REGION,
        "CFBundleLocalizations": list(LOCALIZATIONS),
    }


def _strings_literal(text: str) -> str:
    """``text`` as the body of a quoted ``.strings`` value (backslash and quote escaped)."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def info_plist_strings_text(language: str) -> str:
    """The content of ``<language>.lproj/InfoPlist.strings`` (UTF-8 text, one key per line)."""
    lines = [
        "/* Generated from jarvis/core/macos_privacy_strings.py - do not edit by hand. */",
    ]
    texts = localized_usage_descriptions(language)
    for key in REQUIRED_USAGE_KEYS:
        lines.append(f'"{key}" = "{_strings_literal(texts[key])}";')
    return "\n".join(lines) + "\n"


def write_localizations(resources_dir: Path) -> list[Path]:
    """Write every ``<lang>.lproj/InfoPlist.strings`` into ``resources_dir``; return the files.

    ``resources_dir`` is a bundle's ``Contents/Resources``. Called by the build
    step that runs before the bundle is signed (the files are part of the seal)
    and when the managed bundle is laid out; never at import time. The write is
    UTF-8, which macOS reads for a ``.strings`` file (Apple's own tooling writes
    UTF-16; UTF-8 acceptance on a real Mac is UNVERIFIED).
    """
    written: list[Path] = []
    for language in LOCALIZED_LANGUAGES:
        directory = resources_dir / f"{language}.lproj"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "InfoPlist.strings"
        target.write_text(info_plist_strings_text(language), encoding="utf-8", newline="\n")
        written.append(target)
    return written
