"""Start the backend: `python -m vajra` (run from the backend folder).

The desktop app sets VAJRA_PORT and VAJRA_PARENT_PID when it launches this process.
"""

import os
import sys
import threading
import time

import uvicorn

from vajra.config import get_settings


def _exit_with_parent(pid: int) -> None:
    """Stop the backend when the desktop app that launched it goes away."""
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0xFFFFFFFF)
    else:
        while os.getppid() == pid:
            time.sleep(1)
    os._exit(0)


def main() -> None:
    settings = get_settings()
    parent_pid = os.environ.get("VAJRA_PARENT_PID")
    if parent_pid:
        threading.Thread(target=_exit_with_parent, args=(int(parent_pid),), daemon=True).start()
    port = int(os.environ.get("VAJRA_PORT", settings.server.port))
    uvicorn.run("vajra.main:app", host=settings.server.host, port=port)


if __name__ == "__main__":
    main()
