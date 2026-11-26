// Saghi.app main executable: runs the Python app inside this process, so
// macOS sees Saghi.app itself (not a bare python3.12) as the owner of the
// menu-bar icon, windows and permissions.
#include <Python.h>
#include <limits.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mach-o/dyld.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n) != 0 || !realpath(exe, real)) return 1;
    char macos[PATH_MAX], contents[PATH_MAX], res[PATH_MAX], venv_py[PATH_MAX], app[PATH_MAX];
    // A clean path (no "..") so sys.executable is .../Contents/Resources/venv/bin/python3.12,
    // which paths.app_resources_dir() recognises as the installed app.
    strlcpy(macos, dirname(real), sizeof macos);       // .../Contents/MacOS
    strlcpy(contents, dirname(macos), sizeof contents); // .../Contents
    snprintf(res, sizeof res, "%s/Resources", contents);
    snprintf(venv_py, sizeof venv_py, "%s/venv/bin/python3.12", res);
    snprintf(app, sizeof app, "%s/app", res);
    setenv("PYTHONPATH", app, 1);

    PyStatus st;
    PyConfig cfg;
    PyConfig_InitPythonConfig(&cfg);
    // The venv's interpreter path makes Python find pyvenv.cfg and use the
    // venv's site-packages, exactly as running venv/bin/python3.12 would.
    st = PyConfig_SetBytesString(&cfg, &cfg.executable, venv_py);
    if (PyStatus_Exception(st)) goto fail;
    st = PyConfig_SetBytesString(&cfg, &cfg.program_name, venv_py);
    if (PyStatus_Exception(st)) goto fail;
    char *args[] = { venv_py, "-m", "saghi.ui.app" };
    st = PyConfig_SetBytesArgv(&cfg, 3, args);
    if (PyStatus_Exception(st)) goto fail;
    st = Py_InitializeFromConfig(&cfg);
    if (PyStatus_Exception(st)) goto fail;
    PyConfig_Clear(&cfg);
    return Py_RunMain();
fail:
    PyConfig_Clear(&cfg);
    Py_ExitStatusException(st);
}
