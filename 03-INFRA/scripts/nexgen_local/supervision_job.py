"""One Windows job object per supervised unit: the kernel ends the tree, not a walk of pids.

One job: put the process this engine starts for a unit inside a named job object
carrying `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so that stopping the unit is one
kernel call instead of a race between the process table and whatever is born while
it is being read. The child is given a handle to the job by duplication, so the job
lives exactly as long as the work does and ends its processes when the last handle
closes.

Two limits of the mechanism are reported, never ignored:

- a process joins a job at creation or by assignment, so a descendant of a member
  that this machine's own job chain lets break away is *not* in it. `terminate`
  compares the job's members with the process table and names those descendants;
  the caller stops them one by one and says how many there were.
- `Win32_Process.Create` makes the provider, not the caller, the parent, so such a
  process is in no job and in no tree: nothing in the process table ties it to the
  unit. The stop reports what it could still see under the job rather than claiming
  the unit is gone.

POSIX keeps today's road: there is no job object, `adopt` and `terminate` say there
is nothing to hold, and the process group opened with `start_new_session` does the
same work. psutil stays the reader of the table on both platforms.
"""
from __future__ import annotations

import ctypes
import os

from . import supervision_processes as processes
from . import supervision_state as state
from .supervision_state import SupervisionError

#: `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: the last handle closing ends every process in the job.
KILL_ON_JOB_CLOSE = 0x00002000
#: The name every job of this engine starts with.
NAME_PREFIX = "nexgen-sup"
#: The `SetInformationJobObject` class that carries `LimitFlags`.
_EXTENDED_LIMIT_INFORMATION = 9
#: The `QueryInformationJobObject` class that lists the job's processes.
_BASIC_PROCESS_ID_LIST = 3
#: `JOB_OBJECT_ALL_ACCESS`: a later command ends a job this one created and closed.
_JOB_OBJECT_ALL_ACCESS = 0x1F001F
#: `PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_DUP_HANDLE | PROCESS_QUERY_LIMITED_INFORMATION`:
#: assign, hand the child a handle, and read back which process was really opened.
_PROCESS_ADOPT = 0x0100 | 0x0001 | 0x0040 | 0x1000
#: `DUPLICATE_SAME_ACCESS`: the child gets the job handle with the access this one has.
_DUPLICATE_SAME_ACCESS = 0x00000002
#: What the kernel answers when the buffer of a query is too small for the job.
_ERROR_MORE_DATA = 234

_DWORD = ctypes.c_uint32
_SIZE_T = ctypes.c_size_t
_LARGE_INTEGER = ctypes.c_int64
_HANDLE = ctypes.c_void_p


class JobError(SupervisionError):
    """The kernel refused a job, or the job it holds cannot be read."""


class _BasicLimitInformation(ctypes.Structure):
    """`JOBOBJECT_BASIC_LIMIT_INFORMATION`, of which only `LimitFlags` is set here."""

    _fields_ = [
        ("per_process_user_time", _LARGE_INTEGER),
        ("per_job_user_time", _LARGE_INTEGER),
        ("limit_flags", _DWORD),
        ("minimum_working_set", _SIZE_T),
        ("maximum_working_set", _SIZE_T),
        ("active_process_limit", _DWORD),
        ("affinity", _SIZE_T),
        ("priority_class", _DWORD),
        ("scheduling_class", _DWORD),
    ]


class _IoCounters(ctypes.Structure):
    """`IO_COUNTERS`, present in the extended limits and never read here."""

    _fields_ = [
        ("read_operations", ctypes.c_uint64),
        ("write_operations", ctypes.c_uint64),
        ("other_operations", ctypes.c_uint64),
        ("read_bytes", ctypes.c_uint64),
        ("write_bytes", ctypes.c_uint64),
        ("other_bytes", ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    """`JOBOBJECT_EXTENDED_LIMIT_INFORMATION`: the structure `KILL_ON_JOB_CLOSE` travels in."""

    _fields_ = [
        ("basic", _BasicLimitInformation),
        ("io", _IoCounters),
        ("process_memory_limit", _SIZE_T),
        ("job_memory_limit", _SIZE_T),
        ("peak_process_memory", _SIZE_T),
        ("peak_job_memory", _SIZE_T),
    ]


class _ProcessIdList(ctypes.Structure):
    """`JOBOBJECT_BASIC_PROCESS_ID_LIST`: how many the job holds, and their pids, room for one."""

    _fields_ = [
        ("assigned", _DWORD),
        ("count", _DWORD),
        ("pids", _SIZE_T * 1),
    ]


def _declare(kernel) -> None:
    """Fix the prototypes, so a 64-bit handle is never truncated to an int."""
    kernel.CreateJobObjectW.restype = _HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel.SetInformationJobObject.restype = ctypes.c_int
    kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, _DWORD]
    kernel.QueryInformationJobObject.restype = ctypes.c_int
    kernel.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, _DWORD, ctypes.c_void_p]
    kernel.OpenJobObjectW.restype = _HANDLE
    kernel.OpenJobObjectW.argtypes = [_DWORD, ctypes.c_int, ctypes.c_wchar_p]
    kernel.AssignProcessToJobObject.restype = ctypes.c_int
    kernel.AssignProcessToJobObject.argtypes = [_HANDLE, _HANDLE]
    kernel.TerminateJobObject.restype = ctypes.c_int
    kernel.TerminateJobObject.argtypes = [_HANDLE, ctypes.c_uint]
    kernel.OpenProcess.restype = _HANDLE
    kernel.OpenProcess.argtypes = [_DWORD, ctypes.c_int, _DWORD]
    kernel.GetCurrentProcess.restype = _HANDLE
    kernel.GetProcessId.restype = _DWORD
    kernel.GetProcessId.argtypes = [_HANDLE]
    kernel.DuplicateHandle.restype = ctypes.c_int
    kernel.DuplicateHandle.argtypes = [
        _HANDLE, _HANDLE, _HANDLE, ctypes.POINTER(_HANDLE), _DWORD, ctypes.c_int, _DWORD,
    ]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [_HANDLE]


#: kernel32 on Windows; on POSIX there is no job object and every public call says so.
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _kernel32 is not None:
    _declare(_kernel32)


def available() -> bool:
    """Whether this machine has job objects at all."""
    return _kernel32 is not None


def job_name(task_id: str, unit: str) -> str:
    """The one job a unit's processes share, named so a later command can end it."""
    return f"{NAME_PREFIX}:{state.slug(task_id)}:{state.slug(unit)}"


def adopt(name: str, pid: int | None) -> str:
    """Put a running process inside the unit's job and give it a handle so the job outlives us.

    The engine closes its own handle at the end: a job nobody holds kills its work the
    moment `sup` returns, so the child receives one by duplication and the job lives
    exactly as long as the work does, ending what is left of the tree at the last.

    Returns "" when the turn is held, or the reason it is not. A process that cannot
    be opened is not a failure — a one-shot turn can be over, and this command never
    holds a process it did not start — but a kernel that refuses a duplicate or an
    assignment leaves the turn outside the job, and that is said, not dropped.
    """
    if not name or not available() or not pid:
        return ""
    handle = _create(name)
    process = _kernel32.OpenProcess(_PROCESS_ADOPT, False, int(pid))
    if process and _kernel32.GetProcessId(process) != int(pid):
        # Windows drops a pid's two low bits: 4321 opens 4320, someone else's process.
        _close(process)
        process = None
    if not process:
        _close(handle)
        return ""
    try:
        if not _kernel32.DuplicateHandle(
            _kernel32.GetCurrentProcess(), handle, process,
            ctypes.byref(_HANDLE()), 0, False, _DUPLICATE_SAME_ACCESS,
        ):
            return f"handle del job non duplicabile nel processo {pid}: {_text()}"
        if not _kernel32.AssignProcessToJobObject(handle, process):
            return f"processo {pid} non assegnabile al job: {_text()}"
    finally:
        _close(process)
        _close(handle)
    return ""


def held(name: str) -> list[int] | None:
    """The processes this job holds right now, or None when that name holds no job.

    The caller asks before ending anything: a job is one more way to stop a tree, and
    what protection the tab path applies (never the shared server) must apply here too.
    """
    handle = _open(name)
    if not handle:
        return None
    try:
        return _members(handle)
    finally:
        _close(handle)


def terminate(name: str, table: list[dict]) -> dict:
    """End the unit's job in one call and name the descendants the job never held.

    The strays are read before the termination, because afterwards there is nothing
    left to read: they are the rows under a member that the job did not own, and the
    caller stops them one by one with psutil. No job of this name is not a failure:
    a unit whose work ran in a tab has none.
    """
    handle = _open(name)
    if not handle:
        return {"job": False, "members": [], "strays": []}
    try:
        members = _members(handle)
        held = set(members)
        strays = [
            row["pid"] for _, row in processes.subtree(table, members) if row["pid"] not in held
        ]
        if not _kernel32.TerminateJobObject(handle, 1):
            raise _refused("chiusura", name)
    finally:
        _close(handle)
    return {"job": True, "members": members, "strays": strays}


def _create(name: str) -> int:
    """The unit's job, created or opened, and told to end its processes with its last handle."""
    handle = _kernel32.CreateJobObjectW(None, name)
    if not handle:
        raise _refused("creazione", name)
    try:
        limits = _ExtendedLimitInformation()
        limits.basic.limit_flags = KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            raise _refused("impostazione di kill-on-close", name)
    except BaseException:
        _close(handle)
        raise
    return handle


def _open(name: str) -> int:
    """The job a previous command created, or 0 when that name holds no job (or no kernel has jobs)."""
    if not available():
        return 0
    return _kernel32.OpenJobObjectW(_JOB_OBJECT_ALL_ACCESS, False, name)


def _members(handle: int) -> list[int]:
    """Every pid the job holds right now, as the kernel lists them.

    The kernel writes into a buffer of the caller's size: one too small for the
    structure itself is refused as a bad length, and one too small for the pids is
    `ERROR_MORE_DATA` with the size required. So the first attempt carries room for
    one pid and a bigger job is asked for again at the size the kernel names: a
    unit's tree has been over a thousand processes on this machine, and a fixed
    buffer would refuse to read exactly the job that most needs ending.
    """
    head = _ProcessIdList()
    needed = _DWORD(0)
    if _kernel32.QueryInformationJobObject(
        handle, _BASIC_PROCESS_ID_LIST, ctypes.byref(head), ctypes.sizeof(head),
        ctypes.byref(needed),
    ):
        return [int(head.pids[index]) for index in range(head.count)]
    if ctypes.get_last_error() != _ERROR_MORE_DATA:
        raise _refused("lettura dei processi", "")
    listing = ctypes.create_string_buffer(max(int(needed.value), ctypes.sizeof(head)))
    if not _kernel32.QueryInformationJobObject(
        handle, _BASIC_PROCESS_ID_LIST, listing, len(listing), None,
    ):
        raise _refused("lettura dei processi", "")
    count = ctypes.cast(listing, ctypes.POINTER(_DWORD))[1]
    pids = ctypes.cast(ctypes.addressof(listing) + 2 * ctypes.sizeof(_DWORD), ctypes.POINTER(_SIZE_T))
    return [int(pids[index]) for index in range(count)]


def _refused(action: str, name: str) -> JobError:
    """The kernel's own reason, said in Italian, with the code kept for the diagnosis."""
    error = ctypes.get_last_error()
    detail = ctypes.FormatError(error).strip() or "errore sconosciuto"
    where = f"job «{name}»: " if name else ""
    return JobError(f"{where}{action} rifiutata dal kernel ({detail}, codice {error})")


def _text() -> str:
    """The kernel's last error as one short clause, for a sentence a person reads."""
    error = ctypes.get_last_error()
    detail = ctypes.FormatError(error).strip() or "errore sconosciuto"
    return f"{detail}, codice {error}"


def _close(handle: int) -> None:
    """Release one handle; the job ends when the last one goes."""
    if handle:
        _kernel32.CloseHandle(handle)
