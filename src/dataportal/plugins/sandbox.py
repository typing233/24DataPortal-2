"""Python-level permission sandbox for plugins."""
from __future__ import annotations

import importlib
import sys
from typing import Any

BLOCKED_MODULES = frozenset([
    "subprocess",
    "ctypes",
    "multiprocessing",
    "signal",
    "resource",
    "pty",
    "fcntl",
    "termios",
])

RESTRICTED_OS_ATTRS = frozenset([
    "system",
    "popen",
    "exec",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawn",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "fork",
    "forkpty",
    "kill",
    "killpg",
    "remove",
    "unlink",
    "rmdir",
])

# Core packages that plugins must not shadow or replace
PROTECTED_PACKAGES = frozenset([
    "starlette",
    "jinja2",
    "markupsafe",
    "aiosqlite",
    "dataportal",
])


class PluginImportBlocker:
    """Meta path finder that blocks dangerous imports for sandboxed plugins."""

    def __init__(self, allowed_permissions: list[str] | None = None):
        self._blocked = set(BLOCKED_MODULES)
        if allowed_permissions and "subprocess" in allowed_permissions:
            self._blocked.discard("subprocess")
        self._active = False

    def activate(self):
        self._active = True
        if self not in sys.meta_path:
            sys.meta_path.insert(0, self)

    def deactivate(self):
        self._active = False
        if self in sys.meta_path:
            sys.meta_path.remove(self)

    def find_module(self, fullname: str, path: Any = None):
        if not self._active:
            return None
        top_module = fullname.split(".")[0]
        if top_module in self._blocked:
            return self
        return None

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        if not self._active:
            return None
        top_module = fullname.split(".")[0]
        if top_module in self._blocked:
            raise ImportError(
                f"Plugin sandbox: import of '{fullname}' is not permitted. "
                f"Declare required permissions in plugin metadata."
            )
        return None

    def load_module(self, fullname: str):
        raise ImportError(
            f"Plugin sandbox: import of '{fullname}' is not permitted. "
            f"Declare required permissions in plugin metadata."
        )


class PluginModuleIsolator:
    """Prevents plugins from replacing core modules already in sys.modules.

    When active, captures what modules exist before plugin code runs.
    After plugin code completes, any new modules the plugin loaded stay, but
    if the plugin somehow replaced a protected module, it gets restored.
    """

    def __init__(self):
        self._snapshot: dict[str, Any] = {}

    def capture(self):
        self._snapshot = {
            name: mod for name, mod in sys.modules.items()
            if any(name == p or name.startswith(p + ".") for p in PROTECTED_PACKAGES)
        }

    def restore(self):
        for name, mod in self._snapshot.items():
            if sys.modules.get(name) is not mod:
                sys.modules[name] = mod
        self._snapshot = {}


class SandboxContext:
    """Context manager that activates import blocking during plugin execution."""

    def __init__(self, permissions: list[str] | None = None):
        self._blocker = PluginImportBlocker(permissions)
        self._isolator = PluginModuleIsolator()

    def __enter__(self):
        self._isolator.capture()
        self._blocker.activate()
        return self

    def __exit__(self, *args):
        self._blocker.deactivate()
        self._isolator.restore()


def run_sandboxed(func, *args, permissions: list[str] | None = None, **kwargs):
    """Run a function with import restrictions applied."""
    with SandboxContext(permissions):
        return func(*args, **kwargs)


async def run_sandboxed_async(coro_func, *args, permissions: list[str] | None = None, **kwargs):
    """Run an async function with import restrictions applied."""
    with SandboxContext(permissions):
        return await coro_func(*args, **kwargs)
