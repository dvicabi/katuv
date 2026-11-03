"""FFmpeg/ffprobe helpers: discovery, probing, subtitle burn-in."""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

FFMPEG_INSTALL_HINT = {
    "win32": "winget install Gyan.FFmpeg",
    "darwin": "brew install ffmpeg",
}.get(sys.platform, "sudo apt install ffmpeg  (or your distro's equivalent)")


class FFmpegNotFound(RuntimeError):
    pass


def find_tool(name: str) -> str:
    override = os.environ.get(f"AUTOCAPTION_{name.upper()}")
    if override and Path(override).is_file():
        return override
    from .paths import tools_dir
    # A validated app-managed build also repairs an incompatible system build.
    for managed in sorted(tools_dir().glob(f'ffmpeg*/bin/{name}.exe'), reverse=True):
        if managed.is_file():
            return str(managed)
    path = shutil.which(name)
    if path:
        return path
    candidates: list[str] = []
    # Optional portable installation beside the checkout.
    candidates += [str(p) for p in (Path(__file__).resolve().parent.parent / ".tools").glob(f"ffmpeg*/bin/{name}.exe")]
    if sys.platform == "win32":
        # a fresh winget install may not be on this shell's PATH yet
        candidates.append(os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Links\{name}.exe"))
        candidates += glob.glob(
            os.path.expandvars(
                rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\{name}.exe"
            )
        )
    elif sys.platform == "darwin":
        candidates += [f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    raise FFmpegNotFound(f"{name} not found. Install it with:  {FFMPEG_INSTALL_HINT}")


def probe_video_size(media_path: str | Path) -> tuple[int, int] | None:
    """Return (width, height) of the video stream, or None for audio-only input."""
    result = subprocess.run(
        [
            find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-show_streams", "-of", "json", str(media_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"Cannot inspect media: {result.stderr}")
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return None
    stream = streams[0]
    if stream.get("disposition", {}).get("attached_pic"):
        return None
    width, height = stream["width"], stream["height"]
    rotation = next((s["rotation"] for s in stream.get("side_data_list", []) if "rotation" in s),
                    stream.get("tags", {}).get("rotate", 0))
    if round(float(rotation)) % 180:
        width, height = height, width
    return width, height


def probe_duration(path: str | Path) -> float:
    result = subprocess.run([find_tool("ffprobe"), "-v", "error", "-show_entries",
                             "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                            capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def extract_audio(path: str | Path) -> bytes:
    """Decode a temporary alignment view; the source audio is never modified."""
    result = subprocess.run([find_tool("ffmpeg"), "-v", "error", "-i", str(path),
                             "-map", "0:a:0", "-ac", "1", "-ar", "16000",
                             "-af", "aresample=async=1:first_pts=0",
                             "-f", "f32le", "pipe:1"], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


@contextmanager
def subtitle_workspace(ass_path: Path, fontsdir: Path | None):
    """Simple relative filter paths avoid FFmpeg's two layers of path escaping."""
    with tempfile.TemporaryDirectory(prefix="autocaption-") as temp:
        root = Path(temp)
        shutil.copyfile(ass_path, root / "captions.ass")
        vf = "ass=filename=captions.ass:shaping=complex"
        if fontsdir:
            shutil.copytree(fontsdir, root / "fonts")
            vf += ":fontsdir=fonts"
        yield root, vf


def _filter_path(path: str | Path) -> str:
    """Escape a Windows path for use inside an ffmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", "\\:")


EXPORT_RESOLUTIONS = ('source', '720', '1080', '1440', '2160')


def export_dimensions(width: int, height: int, resolution: str = 'source') -> tuple[int, int]:
    """Resolution names refer to the short edge, for portrait and landscape alike."""
    if resolution not in EXPORT_RESOLUTIONS or width <= 0 or height <= 0:
        raise ValueError('בחרו רזולוציית ייצוא תקינה')
    if resolution == 'source':
        return width, height
    ratio = int(resolution) / min(width, height)
    # H.264 4:2:0 needs even dimensions. FFmpeg retains the precise display ratio
    # in the sample aspect ratio when the long edge needs rounding.
    return tuple(max(2, 2 * int(dimension * ratio / 2 + .5)) for dimension in (width, height))


def subtitle_filter(vf: str, output_size: tuple[int, int] | None = None) -> str:
    if output_size is None:
        return vf
    if len(output_size) != 2 or any(type(v) is not int or v < 2 or v % 2 for v in output_size):
        raise ValueError('מידות הייצוא חייבות להיות זוגיות וחיוביות')
    # Scale the image before libass draws the glyphs at output resolution. ASS
    # PlayRes keeps placement, text size and animations proportional to the frame.
    return f'scale={output_size[0]}:{output_size[1]}:flags=lanczos,' + vf


def burn_subtitles(
    video: str | Path,
    ass_path: str | Path,
    output: str | Path,
    fontsdir: str | Path | None = None,
    nvenc: bool = False,
    progress: Callable[[float], None] | None = None,
    output_size: tuple[int, int] | None = None,
) -> None:
    video, output = Path(video).resolve(), Path(output).resolve()
    if video == output:
        raise ValueError("Output must be different from the source video.")
    if nvenc:
        vcodec = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19"]
    else:
        vcodec = ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]
    duration = probe_duration(video)
    with subtitle_workspace(Path(ass_path), Path(fontsdir) if fontsdir else None) as (cwd, vf):
        vf = subtitle_filter(vf, output_size)
        # Stage beside the destination so successful completion can be atomic.
        fd, staged = tempfile.mkstemp(prefix=".autocaption-", suffix=".mp4", dir=output.parent)
        os.close(fd)
        proc = None
        try:
            with tempfile.TemporaryFile() as errors:
                proc = subprocess.Popen([
                    find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                    "-nostdin", "-progress", "pipe:1", "-nostats", "-i", str(video),
                    "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0",
                    "-vf", vf, *vcodec, "-fps_mode", "passthrough",
                    "-c:a", "copy", "-movflags", "+faststart", staged,
                ], cwd=cwd, stdout=subprocess.PIPE, stderr=errors, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for line in proc.stdout:
                    if line.startswith("out_time_us=") and progress:
                        try:
                            progress(min(0.999, max(0, int(line.strip().split("=")[1]) / 1e6 / duration)))
                        except ValueError:
                            pass
                if proc.wait():
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode("utf-8", errors="replace"))
            os.replace(staged, output)
            if progress:
                progress(1.0)
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait()
            if os.path.exists(staged):
                os.unlink(staged)
