"""ffmpeg, for the one job the app cannot do with the window alone: turning an
episode it cannot show into one it can (library/copies.py, design/playable-copies.md).

The binary ships with the app — imageio-ffmpeg's wheel carries one per platform,
and the frozen sidecar takes it along; a system ffmpeg is the fallback. It only
ever runs as a subprocess, so a missing one is a fact the UI states, never a
crash, and a conversion the app is told to stop is a process it kills.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class ConvertError(Exception):
    """ffmpeg could not make a playable copy of this file."""


class Cancelled(Exception):
    """The conversion was stopped before it finished."""


@lru_cache(maxsize=1)
def ffmpeg() -> str | None:
    """Where the ffmpeg binary is — found, never run. Asking it for its version
    (what imageio-ffmpeg's own lookup does) is a process spawn, and on a first
    run a virus scan of 80 MB, on a path every write of a title passes through."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return shutil.which("ffmpeg")
    for exe in sorted((Path(imageio_ffmpeg.__file__).parent / "binaries").glob("ffmpeg-*")):
        if os.name != "nt" and not os.access(exe, os.X_OK):
            try:
                exe.chmod(exe.stat().st_mode | 0o111)  # a wheel unpacked without its mode
            except OSError:
                continue
        return str(exe)
    return shutil.which("ffmpeg")


# A conversion runs for minutes at full tilt on every core. It must neither
# flash a console window on Windows nor take the machine from the app it serves.
# (Elsewhere the priority is lowered once the process exists — `_tie_to_this_process`
# — since a preexec_fn is not safe in a sidecar that runs threads.)
_SPAWN: dict = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS}
                if os.name == "nt" else {})


@lru_cache(maxsize=1)
def _job() -> int | None:
    """One Windows job object for every ffmpeg this process starts, set to kill
    them when its last handle closes — which is when this process ends, however
    it ends. The shell stops the sidecar with TerminateProcess, no shutdown runs,
    and on Windows a child outlives its parent unless something ties them: the
    conversion would go on loading the GPU and writing into the vault after the
    app is gone. (Elsewhere the shell's SIGTERM is a clean shutdown, and closing
    the library stops the conversion itself.)"""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = Extended()
    limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        return None  # 9: JobObjectExtendedLimitInformation
    return job


def _tie_to_this_process(proc: subprocess.Popen) -> None:
    if os.name != "nt":
        try:
            os.setpriority(os.PRIO_PROCESS, proc.pid, 10)
        except OSError:
            pass
        return
    import ctypes
    from ctypes import wintypes

    job = _job()
    if job:
        assign = ctypes.WinDLL("kernel32").AssignProcessToJobObject
        assign.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        assign(job, int(proc._handle))  # type: ignore[attr-defined]  # Popen's process handle


@dataclass(frozen=True)
class Streams:
    """What ffmpeg says a file holds: the first picture and sound stream."""
    duration: float
    video: str
    pix_fmt: str
    audio: str


_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM = re.compile(r"Stream #\d+:\d+.*?: (Video|Audio): (\w+)(.*)")
_PIX_FMT = re.compile(r"\b(yuvj?4\d\dp\w*|nv\d\d|p0\d\d\w*|gray\w*|gbrp\w*|[rb]gb\w+)\b")


def inspect(src: Path) -> Streams:
    exe = ffmpeg()
    if exe is None:
        raise ConvertError("ffmpeg is not available")
    done = subprocess.run([exe, "-hide_banner", "-nostdin", "-i", str(src)],
                          capture_output=True, text=True, errors="replace", **_SPAWN)
    return parse_streams(done.stderr)


def parse_streams(text: str) -> Streams:
    duration, video, pix_fmt, audio = 0.0, "", "", ""
    if m := _DURATION.search(text):
        duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    for line in text.splitlines():
        m = _STREAM.search(line)
        if m is None or "(attached pic)" in line:  # a cover image is not the picture
            continue
        if m[1] == "Video" and not video:
            video = m[2]
            pix = _PIX_FMT.search(m[3])
            pix_fmt = pix[1] if pix else ""
        elif m[1] == "Audio" and not audio:
            audio = m[2]
    return Streams(duration, video, pix_fmt, audio)


# What every Chromium decodes in software, so a copy holding it plays anywhere
# the vault is opened — HEVC is never kept, it depends on the graphics.
_COPY_VIDEO = {"h264": {"yuv420p", "yuvj420p"},
               "vp9": {"yuv420p", "yuv420p10le"},
               "av1": {"yuv420p", "yuv420p10le"}}
_COPY_AUDIO = {"aac", "mp3", "opus"}


@dataclass(frozen=True)
class Encoder:
    name: str
    args: tuple[str, ...]


# H.264 High, 8-bit 4:2:0: the one picture every Chromium decodes. The hardware
# encoders come first — a 4K episode in software takes longer than it lasts.
# Quality is set where the copy stops growing faster than it improves: on a 4K
# HEVC 4:4:4 episode NVENC at cq 26 kept luma SSIM 0.997 against the original at
# about its size, where cq 20 bought 0.998 for twice the bytes.
_ENCODERS = (
    Encoder("h264_nvenc", ("-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "26", "-b:v", "0",
                           "-profile:v", "high")),
    Encoder("h264_qsv", ("-preset", "medium", "-global_quality", "24", "-profile:v", "high")),
    Encoder("h264_amf", ("-quality", "quality", "-rc", "cqp", "-qp_i", "23", "-qp_p", "25",
                         "-profile:v", "high")),
    Encoder("h264_videotoolbox", ("-q:v", "60", "-profile:v", "high")),
    Encoder("libx264", ("-preset", "medium", "-crf", "21", "-profile:v", "high")),
    Encoder("h264_mf", ("-rate_control", "quality", "-quality", "90", "-hw_encoding", "1")),
)
# even dimensions (4:2:0 needs them) and no wider than 4K, which is as far as
# H.264 hardware encoders go
_SCALE = "scale=w='trunc(min(3840,iw)/2)*2':h=-2"


@lru_cache(maxsize=1)
def encoders() -> tuple[Encoder, ...]:
    """The H.264 encoders that actually run here — compiled in is not enough: a
    hardware one needs the hardware. Tried once per process on a second of
    black, with the very arguments a conversion uses."""
    exe = ffmpeg()
    if exe is None:
        return ()
    works = []
    for enc in _ENCODERS:
        probe = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=1280x720:r=30:d=1", "-frames:v", "5",
                 "-c:v", enc.name, *enc.args, "-pix_fmt", "yuv420p", "-f", "null", "-"]
        try:
            if subprocess.run(probe, capture_output=True, timeout=30, **_SPAWN).returncode == 0:
                works.append(enc)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return tuple(works)


def convert(src: Path, dst: Path, *, progress: Callable[[float], None],
            stop: threading.Event) -> dict:
    """Write a playable mp4 of `src` to `dst` — any name, the muxer is named. A
    picture the window already decodes is carried over untouched (only the
    container changes); anything else is encoded to H.264. Returns what the
    copy holds: {"codec", "audio", "remuxed"}."""
    exe = ffmpeg()
    if exe is None:
        raise ConvertError("ffmpeg is not available")
    if stop.is_set():
        raise Cancelled()
    held = inspect(src)
    if not held.video:
        raise ConvertError("ffmpeg finds no picture in this file")
    if not held.audio:
        sound: list[str] = []
        audio = ""
    elif held.audio in _COPY_AUDIO:
        sound, audio = ["-c:a", "copy"], held.audio
    else:
        sound, audio = ["-c:a", "aac", "-b:a", "192k"], "aac"
    def attempts():
        if held.pix_fmt in _COPY_VIDEO.get(held.video, ()):
            yield ["-c:v", "copy"], held.video, True
        # an encode follows a remux ffmpeg refuses (broken timestamps in an avi
        # or ts), and each hardware encoder the one before it
        for e in encoders():
            yield ["-c:v", e.name, *e.args, "-pix_fmt", "yuv420p", "-vf", _SCALE], "h264", False

    failure = "this ffmpeg has no H.264 encoder that runs here"
    for picture, codec, remuxed in attempts():
        cmd = [exe, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
               "-map", "0:V:0", "-map", "0:a:0?", "-sn", "-dn",
               *picture, *sound, "-movflags", "+faststart", "-f", "mp4",
               "-progress", "pipe:1", "-nostats", str(dst)]
        code, failure = _run(cmd, held.duration, progress, stop)
        if code == 0:
            return {"codec": codec, "audio": audio, "remuxed": remuxed}
    raise ConvertError(failure or "ffmpeg failed")


def _run(cmd: list[str], duration: float, progress: Callable[[float], None],
         stop: threading.Event) -> tuple[int, str]:
    """Run one ffmpeg, reporting progress off its `-progress` lines. A stop kills
    it at once rather than at the next progress line."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, errors="replace", **_SPAWN)
    _tie_to_this_process(proc)
    errors: deque[str] = deque(maxlen=6)
    drain = threading.Thread(target=lambda: errors.extend(proc.stderr or ()), daemon=True)
    drain.start()

    def watch() -> None:
        while proc.poll() is None:
            if stop.wait(0.5):
                proc.kill()
                return

    threading.Thread(target=watch, daemon=True).start()
    for line in proc.stdout or ():
        key, _, value = line.strip().partition("=")
        if key == "out_time_us" and duration > 0 and value.isdigit():
            progress(min(1.0, int(value) / 1e6 / duration))
    code = proc.wait()
    drain.join(timeout=5)
    if stop.is_set():
        raise Cancelled()
    return code, " ".join(e.strip() for e in errors if e.strip())
