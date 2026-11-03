"""Desktop GUI for autocaption: dark-themed single window (customtkinter).

The GUI never reimplements the pipeline — it builds a flag list and runs the
CLI (`python -m autocaption`) as a subprocess, streaming its output into the
log panel. Cancel simply terminates the subprocess.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAS_DND = True
except Exception:  # optional: GUI still works with the Browse button
    HAS_DND = False

from . import media
from .ass_builder import build_ass
from .cli import FONTS_DIR, POSITIONS
from .grouping import CaptionLine
from .styles import PRESETS
from .transcribe import Word
from .alignment import Transcript, media_digest, report_words, save_report, timing_problems, validate_report

CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "autocaption" / "settings.json"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
}

MODEL_CHOICES = {
    "auto (recommended)": None,
    "tiny - fastest": "tiny",
    "base - fast": "base",
    "small - balanced": "small",
    "medium - accurate": "medium",
    "large-v3 - best (multilingual)": "large-v3",
}

LANGUAGES = ["he", "auto", "en", "es", "pt", "fr", "de", "it"]

DEFAULT_SETTINGS = {
    "style": "pop",
    "position": "low",
    "highlight_color": "#FFD700",
    "font": "Noto Sans Hebrew",
    "font_size": "130",
    "words_per_line": "4",
    "language": "he",
    "model_label": "auto (recommended)",
    "caps": False,
    "burn": True,
    "srt": True,
    "nvenc": False,
}


def settings_to_flags(s: dict) -> list[str]:
    """Translate a settings dict into CLI flags for `python -m autocaption`."""
    flags = [
        "--style", s["style"],
        "--position", s["position"],
        "--highlight-color", s["highlight_color"],
        "--font", s["font"],
        "--font-size", s["font_size"],
        "--words-per-line", s["words_per_line"],
        "--language", s["language"],
        "--caps" if s["caps"] else "--no-caps",
    ]
    model = MODEL_CHOICES.get(s["model_label"])
    if model:
        flags += ["--model", model]
    if not s["burn"]:
        flags.append("--no-burn")
    if not s["srt"]:
        flags.append("--no-srt")
    if s["nvenc"]:
        flags.append("--nvenc")
    return flags


def settings_to_style(s: dict):
    """Build a StylePreset from the settings dict (used by the preview)."""
    return replace(
        PRESETS[s["style"]],
        font=s["font"],
        font_size=int(s["font_size"]),
        highlight_color=s["highlight_color"],
        caps=bool(s["caps"]),
        pos_y=POSITIONS[s["position"]],
    )


def render_preview(style, out_png: Path, width: int = 540, height: int = 960,
                   text: str = "שלום, Reels 2026!") -> None:
    """Use the production ASS/libass renderer, including real Hebrew bidi."""
    tokens = Transcript(text).tokens[:4]
    line = CaptionLine([Word(token, i * 10.0, (i + 1) * 10.0) for i, token in enumerate(tokens)])
    ass_file = out_png.with_suffix(".ass")
    ass_file.write_text(build_ass([line], style, width, height), encoding="utf-8")
    with media.subtitle_workspace(ass_file, FONTS_DIR) as (cwd, vf):
        subprocess.run(
            [media.find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c=0x1a2436:s={width}x{height}:d=40:r=1",
             "-ss", str(min(15, len(tokens) * 10 - 5)), "-frames:v", "1", "-vf", vf, str(out_png.resolve())],
            check=True, capture_output=True, cwd=cwd, creationflags=CREATE_NO_WINDOW)


def _reveal_in_file_manager(path: Path) -> None:
    """Open the system file manager at *path*, selecting it when possible."""
    if sys.platform == "win32":
        args = ["explorer", "/select,", str(path)] if path.is_file() else ["explorer", str(path)]
    elif sys.platform == "darwin":
        args = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
    else:
        args = ["xdg-open", str(path if path.is_dir() else path.parent)]
    subprocess.Popen(args)


def available_fonts() -> list[str]:
    """Bundled fonts first, then installed system font families."""
    bundled = ["Noto Sans Hebrew", "Anton"]
    system = sorted(
        {name for name in tkfont.families() if not name.startswith("@")},
        key=str.lower,
    )
    return bundled + [f for f in system if f not in bundled]


if HAS_DND:

    class _AppBase(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._dnd_ok = False
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ok = True
            except Exception:  # native tkdnd extension missing: browse-only
                pass

else:
    _AppBase = ctk.CTk


class App(_AppBase):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Auto-Caption · Hebrew Reels")
        self.geometry("960x940")
        self.minsize(900, 860)

        self.files: list[Path] = []
        self._worker_thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._last_output: str | None = None
        self._log_q: queue.Queue = queue.Queue()
        self._preview_photo = None  # keep a reference so Tk doesn't GC the image
        self._review_context = None
        self._pending_report = None
        self._job_temp = tempfile.TemporaryDirectory(prefix="autocaption-transcript-")

        self.dnd_available = HAS_DND and getattr(self, "_dnd_ok", False)
        self._build_ui()
        self._load_settings()

        if self.dnd_available:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._drain_job = self.after(100, self._drain_log)
        self.after(400, self._check_ffmpeg)

    def _check_ffmpeg(self) -> None:
        try:
            media.find_tool("ffmpeg")
        except media.FFmpegNotFound:
            self._log(
                "[warn] FFmpeg not found - captions can't be burned into video "
                "(subtitle files still work).\n"
                f"[warn] Install it with:  {media.FFMPEG_INSTALL_HINT}\n\n"
            )
            messagebox.showwarning(
                "FFmpeg not found",
                "FFmpeg is needed to burn captions into video and to render previews.\n\n"
                f"Install it with:\n    {media.FFMPEG_INSTALL_HINT}\n\n"
                "You can still transcribe and generate subtitle files without it.",
            )

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        # -- drop zone
        drop_text = (
            "Drop video/audio files here\nor click to browse"
            if self.dnd_available
            else "Click to browse for video/audio files"
        )
        self.drop_zone = ctk.CTkButton(
            self,
            text=drop_text,
            height=84,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=("gray20", "gray17"),
            hover_color=("gray25", "gray22"),
            border_width=2,
            border_color="gray35",
            command=self._browse,
        )
        self.drop_zone.grid(row=0, column=0, sticky="ew", **pad)

        # -- queue
        queue_frame = ctk.CTkFrame(self)
        queue_frame.grid(row=1, column=0, sticky="ew", **pad)
        queue_frame.grid_columnconfigure(0, weight=1)
        self.queue_label = ctk.CTkLabel(queue_frame, text="Queue: empty", anchor="w")
        self.queue_label.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        self.queue_box = ctk.CTkTextbox(queue_frame, height=64, activate_scrollbars=True)
        self.queue_box.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        self.queue_box.configure(state="disabled")
        ctk.CTkButton(queue_frame, text="Clear", width=64, command=self._clear_queue).grid(
            row=0, column=1, rowspan=2, padx=10, pady=6
        )

        # Transcript is optional; once supplied it is the only source of words.
        transcript_frame = ctk.CTkFrame(self)
        transcript_frame.grid(row=2, column=0, sticky="ew", **pad)
        transcript_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(transcript_frame, text="Exact Hebrew transcript (optional) · one video at a time",
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=10)
        ctk.CTkButton(transcript_frame, text="Upload TXT", width=110,
                      command=self._upload_transcript).grid(row=0, column=1, padx=10, pady=5)
        self.transcript_box = ctk.CTkTextbox(transcript_frame, height=95, font=("Arial", 18), wrap="word")
        self.transcript_box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        ctk.CTkLabel(transcript_frame,
                     text="Pasted words stay exact. Generate → align → review timings → export. Blank = multilingual ASR.",
                     anchor="w").grid(row=2, column=0, columnspan=2, sticky="ew", padx=10)
        ctk.CTkButton(transcript_frame, text="Resume timing report", command=self._resume_report,
                      width=180).grid(row=3, column=0, sticky="w", padx=10, pady=5)

        # -- settings grid
        grid = ctk.CTkFrame(self)
        grid.grid(row=3, column=0, sticky="ew", **pad)
        for col in (1, 3):
            grid.grid_columnconfigure(col, weight=1)

        def label(text, row, col):
            ctk.CTkLabel(grid, text=text, anchor="e").grid(row=row, column=col, sticky="e", padx=(10, 4), pady=5)

        label("Style", 0, 0)
        self.style_var = ctk.StringVar()
        ctk.CTkOptionMenu(grid, variable=self.style_var, values=sorted(PRESETS)).grid(
            row=0, column=1, sticky="ew", padx=4, pady=5
        )

        label("Position", 0, 2)
        self.position_var = ctk.StringVar()
        ctk.CTkOptionMenu(grid, variable=self.position_var, values=sorted(POSITIONS)).grid(
            row=0, column=3, sticky="ew", padx=(4, 10), pady=5
        )

        label("Font", 1, 0)
        self.font_var = ctk.StringVar()
        self.font_menu = ctk.CTkComboBox(grid, variable=self.font_var, values=available_fonts())
        self.font_menu.grid(row=1, column=1, sticky="ew", padx=4, pady=5)

        label("Font size", 1, 2)
        self.font_size_var = ctk.StringVar()
        ctk.CTkEntry(grid, textvariable=self.font_size_var).grid(row=1, column=3, sticky="ew", padx=(4, 10), pady=5)

        label("Highlight", 2, 0)
        self.highlight_var = ctk.StringVar()
        self.color_button = ctk.CTkButton(grid, text="#FFD700", command=self._pick_color, text_color="black")
        self.color_button.grid(row=2, column=1, sticky="ew", padx=4, pady=5)

        label("Words/line", 2, 2)
        self.words_var = ctk.StringVar()
        ctk.CTkOptionMenu(grid, variable=self.words_var, values=[str(n) for n in range(1, 7)]).grid(
            row=2, column=3, sticky="ew", padx=(4, 10), pady=5
        )

        label("Model", 3, 0)
        self.model_var = ctk.StringVar()
        ctk.CTkOptionMenu(grid, variable=self.model_var, values=list(MODEL_CHOICES)).grid(
            row=3, column=1, sticky="ew", padx=4, pady=5
        )

        label("Language", 3, 2)
        self.language_var = ctk.StringVar()
        ctk.CTkComboBox(grid, variable=self.language_var, values=LANGUAGES).grid(
            row=3, column=3, sticky="ew", padx=(4, 10), pady=5
        )

        switches = ctk.CTkFrame(grid, fg_color="transparent")
        switches.grid(row=4, column=0, columnspan=4, sticky="ew", padx=6, pady=(4, 8))
        self.caps_var = ctk.BooleanVar()
        self.burn_var = ctk.BooleanVar()
        self.srt_var = ctk.BooleanVar()
        self.nvenc_var = ctk.BooleanVar()
        for text, var in (
            ("UPPERCASE", self.caps_var),
            ("Burn video", self.burn_var),
            ("DaVinci SRT", self.srt_var),
            ("NVENC (fast export)", self.nvenc_var),
        ):
            ctk.CTkSwitch(switches, text=text, variable=var).pack(side="left", padx=10)

        # -- action row
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", **pad)
        actions.grid_columnconfigure(1, weight=1)
        self.preview_button = ctk.CTkButton(actions, text="Preview style", command=self._preview)
        self.preview_button.grid(row=0, column=0, padx=(0, 8))
        self.progress = ctk.CTkProgressBar(actions, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=8)
        self.progress.set(0)
        self.open_button = ctk.CTkButton(actions, text="Open output folder", command=self._open_output, state="disabled")
        self.open_button.grid(row=0, column=2, padx=8)
        self.cancel_button = ctk.CTkButton(
            actions, text="Cancel", command=self._cancel, state="disabled", fg_color="#8a3535", hover_color="#a54040"
        )
        self.cancel_button.grid(row=0, column=3, padx=8)
        self.generate_button = ctk.CTkButton(
            actions, text="Generate", command=self._generate, font=ctk.CTkFont(weight="bold"), width=130
        )
        self.generate_button.grid(row=0, column=4)

        # -- log
        self.log_box = ctk.CTkTextbox(self, wrap="word")
        self.log_box.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log_box.configure(state="disabled")

    # ---------------------------------------------------------- settings

    def _gather_settings(self) -> dict:
        return {
            "style": self.style_var.get(),
            "position": self.position_var.get(),
            "highlight_color": self.highlight_var.get(),
            "font": self.font_var.get(),
            "font_size": self.font_size_var.get().strip() or DEFAULT_SETTINGS["font_size"],
            "words_per_line": self.words_var.get(),
            "language": self.language_var.get().strip() or "he",
            "model_label": self.model_var.get(),
            "caps": self.caps_var.get(),
            "burn": self.burn_var.get(),
            "srt": self.srt_var.get(),
            "nvenc": self.nvenc_var.get(),
        }

    def _apply_settings(self, s: dict) -> None:
        self.style_var.set(s["style"])
        self.position_var.set(s["position"])
        self._set_color(s["highlight_color"])
        self.font_var.set(s["font"])
        self.font_size_var.set(str(s["font_size"]))
        self.words_var.set(str(s["words_per_line"]))
        self.language_var.set(s["language"])
        self.model_var.set(s["model_label"] if s["model_label"] in MODEL_CHOICES else "auto (recommended)")
        self.caps_var.set(bool(s["caps"]))
        self.burn_var.set(bool(s["burn"]))
        self.srt_var.set(bool(s["srt"]))
        self.nvenc_var.set(bool(s["nvenc"]))

    def _load_settings(self) -> None:
        settings = dict(DEFAULT_SETTINGS)
        try:
            settings.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self._apply_settings(settings)

    def _save_settings(self) -> None:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(self._gather_settings(), indent=2), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------- file queue

    def _browse(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTENSIONS))
        paths = filedialog.askopenfilenames(
            title="Choose video or audio files",
            filetypes=[("Media files", exts), ("All files", "*.*")],
        )
        self._add_files(paths)

    def _on_drop(self, event) -> None:
        self._add_files(self.tk.splitlist(event.data))

    def _add_files(self, paths) -> None:
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                continue
            if path.suffix.lower() not in MEDIA_EXTENSIONS:
                self._log(f"[skip] {path.name}: not a recognized media file\n")
                continue
            if path not in self.files:
                self.files.append(path)
        self._refresh_queue()

    def _clear_queue(self) -> None:
        self.files.clear()
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        self.queue_box.configure(state="normal")
        self.queue_box.delete("1.0", "end")
        self.queue_box.insert("1.0", "\n".join(f.name for f in self.files))
        self.queue_box.configure(state="disabled")
        count = len(self.files)
        self.queue_label.configure(text=f"Queue: {count} file{'s' if count != 1 else ''}" if count else "Queue: empty")

    # ----------------------------------------------------------- color

    def _set_color(self, hex_color: str) -> None:
        self.highlight_var.set(hex_color)
        luminance = sum(int(hex_color.lstrip("#")[i : i + 2], 16) * w for i, w in ((0, 0.299), (2, 0.587), (4, 0.114)))
        self.color_button.configure(
            text=hex_color, fg_color=hex_color, hover_color=hex_color,
            text_color="black" if luminance > 128 else "white",
        )

    def _pick_color(self) -> None:
        _, hex_color = colorchooser.askcolor(color=self.highlight_var.get(), title="Highlight color")
        if hex_color:
            self._set_color(hex_color.upper())

    # --------------------------------------------------------- preview

    def _upload_transcript(self) -> None:
        filename = filedialog.askopenfilename(title="Exact Hebrew transcript", filetypes=[("UTF-8 text", "*.txt")])
        if filename:
            try:
                text = Path(filename).read_text(encoding="utf-8-sig")
                Transcript(text)
            except (OSError, UnicodeError, ValueError) as error:
                messagebox.showerror("Transcript", str(error))
                return
            self.transcript_box.delete("1.0", "end")
            self.transcript_box.insert("1.0", text)

    def _resume_report(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        if len(self.files) != 1:
            messagebox.showinfo("Choose one video", "Add the original video and paste/upload its exact transcript first.")
            return
        filename = filedialog.askopenfilename(title="Resume timings", filetypes=[("Timing report", "*.timings.json"), ("JSON", "*.json")])
        if not filename:
            return
        try:
            transcript = Transcript(self.transcript_box.get("1.0", "end-1c"))
            flags = settings_to_flags(self._gather_settings())
            source = Path(self._job_temp.name) / "transcript.txt"
            source.write_text(transcript.text, encoding="utf-8")
            self._review_context = (self.files[0], transcript, source, flags)
            self._timing_editor(Path(filename))
        except Exception as error:
            messagebox.showerror("Timing report", str(error))

    def _preview(self) -> None:
        try:
            style = settings_to_style(self._gather_settings())
            transcript = self.transcript_box.get("1.0", "end-1c")
            if transcript.strip():
                style = replace(style, caps=False)
            out_png = Path(tempfile.gettempdir()) / "autocaption_preview.png"
            render_preview(style, out_png, text=transcript if transcript.strip() else "שלום, Reels 2026!")
        except Exception as error:
            messagebox.showerror("Preview failed", str(error))
            return
        window = ctk.CTkToplevel(self)
        window.title(f"Preview - {style.name}")
        self._preview_photo = tk.PhotoImage(file=str(out_png))
        tk.Label(window, image=self._preview_photo, bd=0, background="#1a2436").pack()
        window.after(50, window.lift)

    # -------------------------------------------------------- pipeline

    def _timing_editor(self, path: Path) -> None:
        if self._review_context is None:
            return
        file, transcript, source, flags = self._review_context
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            validate_report(report, transcript, media_digest(file))
        except Exception as error:
            messagebox.showerror("Cannot open timing report", str(error))
            return
        window = ctk.CTkToplevel(self)
        window.title("Review word timings · text is locked")
        window.geometry("1000x650")
        window.after(50, window.lift)
        window.grab_set()
        unresolved = len(timing_problems(report))
        ctk.CTkLabel(window, text=f"{len(report['words'])} exact words · {unresolved} need correction · seconds from start",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=10)
        ctk.CTkLabel(window, text="Review estimates against the source. Fill every missing time. Timings must be ordered and non-overlapping.").pack()
        table = ctk.CTkScrollableFrame(window)
        table.pack(fill="both", expand=True, padx=12, pady=10)
        entries = []
        for col, title in enumerate(("#", "Immutable word", "Start (s)", "End (s)", "Alignment / reason")):
            ctk.CTkLabel(table, text=title).grid(row=0, column=col, padx=10, pady=5)
        for index, row in enumerate(report["words"], 1):
            color = "#FFD700" if row["status"] == "unaligned" else "gray85"
            ctk.CTkLabel(table, text=str(index), text_color=color).grid(row=index, column=0, padx=5)
            ctk.CTkLabel(table, text=row["text"], font=("Arial", 18), anchor="e",
                         width=190).grid(row=index, column=1, padx=10, pady=4)
            start, end = ctk.CTkEntry(table, width=90), ctk.CTkEntry(table, width=90)
            for col, widget, value in ((2, start, row["start"]), (3, end, row["end"])):
                widget.grid(row=index, column=col, padx=5, pady=4)
                if value is not None:
                    widget.insert(0, f"{value:.3f}")
            ctk.CTkLabel(table, text=row["reason"] or row["status"], text_color=color,
                         wraplength=390, justify="left").grid(row=index, column=4, padx=10, sticky="w")
            entries.append((start, end))
        buttons = ctk.CTkFrame(window)
        buttons.pack(fill="x", padx=12, pady=10)

        def save(export=False):
            try:
                for row, (start, end) in zip(report["words"], entries):
                    values = [float(widget.get()) if widget.get().strip() else None for widget in (start, end)]
                    if values != [row["start"], row["end"]]:
                        row.update(start=values[0], end=values[1], status="manual", reason="")
                validate_report(report, transcript, report["media_sha256"])
                if export:
                    report_words(report)  # validates all words, bounds and order
                save_report(path, report)
                if export:
                    window.destroy()
                    self._pending_report = None
                    self._start_job([file], [*flags, "--transcript-file", str(source), "--timings", str(path)])
                else:
                    messagebox.showinfo("Saved", f"Saved {path.name}. {len(timing_problems(report))} words still need correction.", parent=window)
            except (ValueError, OSError) as error:
                messagebox.showerror("Correct timings before export", str(error), parent=window)

        def play():
            if sys.platform == "win32":
                os.startfile(file)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(file)])

        ctk.CTkButton(buttons, text="Play original video", command=play).pack(side="left", padx=8, pady=8)
        ctk.CTkButton(buttons, text="Save timings", command=save).pack(side="left", padx=8)
        ctk.CTkButton(buttons, text="Export reviewed captions", command=lambda: save(True)).pack(side="right", padx=8)

    def _generate(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        if not self.files:
            messagebox.showinfo("Nothing to do", "Add a video or audio file first.")
            return
        self._save_settings()
        flags = settings_to_flags(self._gather_settings())
        files = list(self.files)
        transcript_text = self.transcript_box.get("1.0", "end-1c")
        self._review_context = None
        self._pending_report = None
        if transcript_text.strip():
            if len(files) != 1:
                messagebox.showinfo("One transcript, one video", "Keep one video in the queue when supplying a transcript.")
                return
            transcript = Transcript(transcript_text)
            source = Path(self._job_temp.name) / "transcript.txt"
            source.write_text(transcript.text, encoding="utf-8")
            self._review_context = (files[0], transcript, source, flags)
            flags = [*flags, "--transcript-file", str(source), "--align-only"]
        self._start_job(files, flags)

    def _start_job(self, files: list[Path], flags: list[str]) -> None:
        self._cancelled = False
        self._last_output = None
        self.generate_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self._worker_thread = threading.Thread(target=self._worker, args=(files, flags), daemon=True)
        self._worker_thread.start()

    def _worker(self, files: list[Path], flags: list[str]) -> None:
        failures = 0
        for i, file in enumerate(files, start=1):
            if self._cancelled:
                break
            self._log_q.put(f"\n=== [{i}/{len(files)}] {file.name} ===\n")
            cmd = [sys.executable, "-u", "-m", "autocaption", str(file), *flags]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    start_new_session=sys.platform != "win32",
                    creationflags=CREATE_NO_WINDOW,
                )
                for line in self._proc.stdout:
                    self._log_q.put(line)
                    if line.startswith("[timings] ") and "--align-only" in flags:
                        self._pending_report = Path(line.strip()[10:])
                    if line.startswith("[progress] "):
                        self._log_q.put(("__progress__", (i - 1 + float(line.split()[1])) / len(files)))
                    match = re.match(r"\[out\] (.+)", line.strip())
                    if match:
                        self._last_output = match.group(1)
                code = self._proc.wait()
                if code != 0 and not self._cancelled:
                    failures += 1
                    self._log_q.put(f"[error] exited with code {code}\n")
            except Exception as error:
                failures += 1
                self._log_q.put(f"[error] {error}\n")
        if self._cancelled:
            self._log_q.put("\n=== Cancelled ===\n")
        else:
            done = len(files) - failures
            self._log_q.put(f"\n=== Done: {done}/{len(files)} file{'s' if len(files) != 1 else ''} processed ===\n")
        self._log_q.put(("__finished__", not failures and not self._cancelled))

    def _cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(self._proc.pid, signal.SIGTERM)

    def _open_output(self) -> None:
        if self._last_output and Path(self._last_output).exists():
            _reveal_in_file_manager(Path(self._last_output))
        elif self.files:
            _reveal_in_file_manager(self.files[0].parent)

    # ------------------------------------------------------------- log

    def _log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _drain_log(self) -> None:
        try:
            while True:
                item = self._log_q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__finished__":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(1 if item[1] else 0)
                    self.generate_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if self._last_output:
                        self.open_button.configure(state="normal")
                    if item[1] and self._pending_report:
                        path, self._pending_report = self._pending_report, None
                        self._timing_editor(path)
                elif isinstance(item, tuple) and item[0] == "__progress__":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress.set(item[1])
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self._drain_job = self.after(100, self._drain_log)

    def _on_close(self) -> None:
        self.after_cancel(self._drain_job)
        self._save_settings()
        self._cancel()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
