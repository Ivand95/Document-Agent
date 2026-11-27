"""Running an allowlisted program (CLI operations, WP6; stdio MCP servers find their executable here too).

- The executable comes from CAPABILITY_BIN_DIR: a name, never a path, resolved and checked to stay inside
  that directory. Unset = nothing runs.
- No shell: the arguments are a list, passed as they are.
- A fresh temporary directory per run is the working directory, HOME and TMPDIR, deleted afterwards.
- The environment is built from scratch: PATH (the bin directory and the system's), LANG, and only what
  the caller passes (the capability's settings and the secrets that program needs).
- Never as root: when the app runs as root, programs run as CAPABILITY_RUN_AS_USER, with root's groups
  dropped; without it they don't run at all.
- Limits: wall-clock timeout (the whole process group is killed), CPU time, memory, size of written
  files, no core dumps, open files; stdout and stderr are read up to a cap (over it, the program is
  stopped and the output marked truncated).
The program can still reach the network: an egress allowlist is a deployment concern (WP4b code tools).
"""
import asyncio
import os
import pwd
import resource
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass

from capabilities.gateway import CapabilityError

BIN_DIR_ENV = "CAPABILITY_BIN_DIR"
RUN_AS_ENV = "CAPABILITY_RUN_AS_USER"
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_OPEN_FILES = 256
READ_CHUNK = 65536


@dataclass(frozen=True)
class Result:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    truncated: bool  # an output went over its cap and the program was stopped
    timed_out: bool
    duration_ms: int


def resolve_executable(name: str) -> str:
    """The executable inside CAPABILITY_BIN_DIR, refusing anything that resolves outside it."""
    base = os.getenv(BIN_DIR_ENV)
    if not base:
        raise CapabilityError("not_available", f"{BIN_DIR_ENV} is not set: no programs can run")
    base = os.path.realpath(base)
    path = os.path.realpath(os.path.join(base, name))
    if os.path.dirname(path) != base or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise CapabilityError("not_available", f"{name!r} is not an executable in {BIN_DIR_ENV}")
    return path


def run_as() -> pwd.struct_passwd | None:
    """The user programs run as: CAPABILITY_RUN_AS_USER when the app is root, else the app's own user."""
    name = os.getenv(RUN_AS_ENV, "").strip()
    if os.geteuid() != 0:
        return None  # can't switch users, and doesn't need to: the app isn't root
    if not name:
        raise CapabilityError("not_available", f"the app runs as root and {RUN_AS_ENV} is not set")
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        raise CapabilityError("not_available", f"{RUN_AS_ENV} names an unknown user") from None
    if user.pw_uid == 0:
        raise CapabilityError("not_available", f"{RUN_AS_ENV} can't be root")
    return user


def base_env(extra: dict | None = None) -> dict:
    env = {"PATH": f"{os.path.realpath(os.getenv(BIN_DIR_ENV) or '/nonexistent')}:{SYSTEM_PATH}",
           "LANG": os.getenv("LANG", "C.UTF-8")}
    env.update(extra or {})
    return env


def _limiter(cpu_seconds: int, memory_bytes: int):
    """Runs in the child before exec. Only setrlimit calls: no imports, no locks."""
    limits = (
        (resource.RLIMIT_CPU, cpu_seconds), (resource.RLIMIT_AS, memory_bytes),
        (resource.RLIMIT_FSIZE, MAX_FILE_BYTES), (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, MAX_OPEN_FILES),
    )

    def apply():
        for kind, value in limits:
            try:
                _, hard = resource.getrlimit(kind)
                value = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(kind, (value, value))
            except (ValueError, OSError):
                pass  # not supported here (e.g. RLIMIT_AS on macOS): the other limits still apply

    return apply


def _kill(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)  # the whole group: children too
    except (ProcessLookupError, PermissionError):
        pass


async def run(argv: list[str], *, env: dict, timeout: float, memory_mb: int = 512,
              max_stdout: int = 1_000_000, max_stderr: int = 16_000) -> Result:
    """Runs argv[0] (already resolved with resolve_executable) with the limits above."""
    user = run_as()
    started = time.monotonic()
    workdir = tempfile.mkdtemp(prefix="capability-")
    try:
        if user is not None:
            os.chown(workdir, user.pw_uid, user.pw_gid)
        os.chmod(workdir, 0o700)
        identity = {"user": user.pw_uid, "group": user.pw_gid, "extra_groups": []} if user else {}
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=workdir, env={**env, "HOME": workdir, "TMPDIR": workdir},
            start_new_session=True, preexec_fn=_limiter(int(timeout) + 1, memory_mb * 1024 * 1024), **identity,
        )
        truncated = False

        async def read(stream, cap: int) -> bytes:
            nonlocal truncated
            data = bytearray()
            while chunk := await stream.read(READ_CHUNK):
                data += chunk
                if len(data) > cap:
                    truncated = True
                    _kill(process)
                    return bytes(data[:cap])
            return bytes(data)

        timed_out = False
        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(read(process.stdout, max_stdout), read(process.stderr, max_stderr), process.wait()),
                timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            _kill(process)
            await process.wait()
            stdout, stderr = b"", b""
        return Result(process.returncode, stdout, stderr, truncated, timed_out,
                      int((time.monotonic() - started) * 1000))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
