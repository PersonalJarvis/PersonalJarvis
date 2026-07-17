/*
 * In-process native stub launcher for the managed-install macOS app bundle
 * (BUG-060).
 *
 * The bundle's CFBundleExecutable must stay a real Mach-O process: a shell
 * script that execs an external virtual-environment Python loses the app's
 * NSBundle identity, so TCC grants attach to "Python" or a terminal instead
 * of Personal Jarvis. This stub embeds CPython inside the app process itself
 * (libpython is linked at build time) and runs the managed entry script, so
 * the stable TCC identity is the app bundle across ordinary source updates.
 *
 * Unlike py2app's bootstrap, this works with framework AND non-framework
 * interpreters (for example uv's python-build-standalone builds): the
 * program name is pointed at the venv interpreter and CPython's own path
 * machinery (getpath + pyvenv.cfg) performs the full venv resolution.
 *
 * Compiled at install time by jarvis/setup/macos_app_bundle.py, which injects
 * the absolute paths as -D string macros:
 *   JARVIS_VENV_PYTHON  - the managed venv's python executable
 *   JARVIS_ENTRY_SCRIPT - jarvis/setup/macos_launcher_entry.py
 */

#include <Python.h>

#include <locale.h>
#include <stdlib.h>

int main(int argc, char *argv[]) {
    /* Finder-launched apps inherit no locale, which would make CPython
     * decode paths and argv as ASCII. Mirror py2app's fix: force UTF-8.
     *
     * ONLY LC_CTYPE — never LC_ALL (BUG-068): a plain `python` binary leaves
     * LC_NUMERIC in the "C" locale, and native libraries rely on that when
     * they format numbers. setlocale(LC_ALL, "") on a German macOS put
     * LC_NUMERIC=de_DE into the whole process and libvosk then emitted
     * `"conf" : 1,000000` — malformed JSON that crash-looped the wake stack. */
    if (getenv("LC_ALL") == NULL && getenv("LC_CTYPE") == NULL && getenv("LANG") == NULL) {
        setenv("LC_CTYPE", "UTF-8", 1);
    }
    setlocale(LC_CTYPE, "");

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);

    /* CPython derives its prefixes (and the venv, via pyvenv.cfg) from the
     * program name, exactly as if the venv interpreter had been started. */
    status = PyConfig_SetBytesString(&config, &config.program_name, JARVIS_VENV_PYTHON);
    if (PyStatus_Exception(status)) {
        goto fail;
    }

    /* argv = {venv python, entry script, forwarded app arguments...} so the
     * embedded interpreter runs the entry script like a command line would. */
    char **embedded_argv = calloc((size_t)argc + 1, sizeof(char *));
    if (embedded_argv == NULL) {
        status = PyStatus_NoMemory();
        goto fail;
    }
    embedded_argv[0] = JARVIS_VENV_PYTHON;
    embedded_argv[1] = JARVIS_ENTRY_SCRIPT;
    for (int i = 1; i < argc; i++) {
        embedded_argv[i + 1] = argv[i];
    }
    status = PyConfig_SetBytesArgv(&config, argc + 1, embedded_argv);
    free(embedded_argv);
    if (PyStatus_Exception(status)) {
        goto fail;
    }

    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) {
        goto fail;
    }
    PyConfig_Clear(&config);
    return Py_RunMain();

fail:
    PyConfig_Clear(&config);
    Py_ExitStatusException(status);
}
