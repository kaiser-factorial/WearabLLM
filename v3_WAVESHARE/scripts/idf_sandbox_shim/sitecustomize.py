"""Opt-in shim for ESP-IDF builds inside the restricted Codex macOS sandbox."""

import os

if os.environ.get("WEARABLLM_IDF_SANDBOX") == "1":
    try:
        import psutil
    except ModuleNotFoundError:
        pass
    else:
        # The component manager falls back to os.getppid(), which is CMake here.
        # Avoid its otherwise equivalent ancestry walk, blocked by this sandbox.
        psutil.Process.parent = lambda self: None
