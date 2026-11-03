"""Local web studio. Reuses the transcript, ASS and FFmpeg pipeline unchanged."""
from __future__ import annotations

import argparse
import copy
import errno
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Literal
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import media, __version__
from .alignment import (Transcript, align_transcript, media_digest, new_report,
                        report_words, save_report, timing_problems, validate_report, display_rows, valid_interval)
from .ass_builder import _fit_size, build_ass
from .cli import FONTS_DIR
from .grouping import group_words
from .srt_builder import build_srt
from .styles import PRESETS, MOTION_REVISION, motion_spec
from .transcribe import transcribe
from .verification import build_evidence, refresh_checks, verify_report, verification_problems
from .models import (BY_ID, DEFAULT_MODEL, SpeechModelError, evidence_cache_name,
                     model_catalog, model_label)
from .hardware import AUTO_MODEL, resolve_selection, run_selected
from .automatic_timing import complete_timings
from .segments import prepare_segments, validate_segments, segment_lines
from .paths import project_dir
from .bootstrap import Preparation, diagnostics

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).with_name("web_static")
BUSY = {"aligning", "verifying", "exporting"}


def installation_identity(root: Path):
    return os.environ.get('AUTOCAPTION_INSTALLATION_ID') or hashlib.sha256(
        str(root.resolve()).casefold().encode('utf-8')).hexdigest()[:32]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style: Literal["pop", "karaoke", "zoom", "float"] = "zoom"
    font_size: int = Field(default=110, ge=40, le=200)
    words_per_line: int = Field(default=4, ge=1, le=6)
    position: float = Field(default=.70, ge=.04, le=.96)
    position_x: float = Field(default=.5, ge=.04, le=.96)
    rotation: float = Field(default=0, ge=-45, le=45)
    motion_strength: float = Field(default=1, ge=0, le=2)
    motion_speed: float = Field(default=1, ge=.5, le=2)
    highlight_color: str = Field(default="#D4F76B", pattern=r"^#[0-9a-fA-F]{6}$")
    text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    outline: float = Field(default=3, ge=0, le=8)
    backdrop_opacity: float = Field(default=0, ge=0, le=.9)
    caption_mode: Literal["words", "segments"] = "words"
    word_animation: bool = True
    export_resolution: Literal['source', '720', '1080', '1440', '2160'] = 'source'


class AlignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript: str = Field(default="", max_length=50000)
    mode: Literal["exact", "auto"] = "exact"


class VerifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolve_manual_conflicts: bool | None = None


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speech_model: str

    @field_validator("speech_model")
    @classmethod
    def supported(cls, value):
        if value != AUTO_MODEL and value not in BY_ID:
            raise ValueError("בחרו מודל תומך עברית מהרשימה")
        return value


class PrepareInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    include_noncommercial: bool = False


class MaintenanceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


class Timing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    start: float | None = Field(default=None, allow_inf_nan=False)
    end: float | None = Field(default=None, allow_inf_nan=False)
    listened: bool = False


class TimingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    words: list[Timing]
    allow_reorder: bool = False
    edit_kind: Literal["manual", "gap_fill", "gap_fill_undo"] = "manual"


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=50000)
    label: str = Field(default="", max_length=80)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class SegmentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segments: list[Segment] = Field(min_length=1, max_length=1000)


def style_from(settings: dict):
    s = Settings(**settings)
    base = PRESETS[s.style]
    motion = {key: round(getattr(base, key) / s.motion_speed) for key in
              ("pop_ms", "entry_ms", "exit_ms", "fade_in_ms", "fade_out_ms", "color_ms")}
    animate_words = s.word_animation and s.caption_mode == "words"
    return replace(base, font="Noto Sans Hebrew", caps=False, word_animation=animate_words,
                   font_size=s.font_size, pos_y=s.position, highlight_color=s.highlight_color,
                   text_color=s.text_color, outline=s.outline, pos_x=s.position_x, rotation=s.rotation,
                   backdrop_opacity=s.backdrop_opacity,
                   pop_scale=100+(base.pop_scale-100)*s.motion_strength if animate_words else 100,
                   pulse=tuple((fraction, 100+(size-100)*s.motion_strength, accel)
                               for fraction, size, accel in base.pulse),
                   entry_y=base.entry_y*s.motion_strength, exit_y=base.exit_y*s.motion_strength, **motion)


class Studio:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption")
        self.futures = {}
        self.maintenance = False
        self.preparing = False
        self.installation_id = installation_identity(root)
        # A previous process cannot still own jobs after a server restart.
        for path in root.glob("*/project.json"):
            try:
                data = self.read_project(path)
            except (ValueError, OSError):
                logging.warning('Unreadable project retained: %s', path.parent.name)
                continue
            if data.get("state") in BUSY:
                data.update(state="error", message="הפעולה נעצרה כשהיישום נסגר. אפשר לנסות שוב.")
                self.write(path, data)

    @staticmethod
    def read_project(path):
        data = json.loads(path.read_text(encoding='utf-8'))
        required = {'id', 'name', 'state', 'settings', 'transcript', 'fingerprint',
                    'width', 'height', 'duration', 'updated'}
        if not isinstance(data, dict) or not required.issubset(data) or data['id'] != path.parent.name:
            raise ValueError('Invalid project record')
        if not all(isinstance(data[key], str) for key in ('name', 'state', 'transcript', 'fingerprint')):
            raise ValueError('Invalid project text')
        if not isinstance(data['settings'], dict):
            raise ValueError('Invalid project settings')
        Settings(**data['settings'])
        for key in ('width', 'height', 'duration', 'updated'):
            if (not isinstance(data[key], (int, float)) or isinstance(data[key], bool)
                    or not math.isfinite(data[key]) or data[key] <= 0):
                raise ValueError('Invalid project dimensions or time')
        return data

    def projects(self):
        records = []
        for path in self.root.glob('*/project.json'):
            if not re.fullmatch(r'[a-f0-9]{32}', path.parent.name):
                continue
            try:
                records.append(self.read_project(path))
            except (ValueError, OSError):
                try:
                    modified = path.stat().st_mtime
                except OSError:
                    modified = 0
                records.append(dict(id=path.parent.name, name='פרויקט שלא ניתן לפתוח',
                    state='damaged', duration=0, updated=modified,
                    message='קובץ הפרויקט פגום. הקבצים המקוריים נשמרו לשחזור.'))
        return sorted(records, key=lambda p: p['updated'], reverse=True)[:30]

    def preferences(self):
        try:
            data = json.loads((self.root / '.preferences.json').read_text(encoding='utf-8'))
            return dict(include_noncommercial=data.get('include_noncommercial') is True)
        except (ValueError, OSError, AttributeError):
            return dict(include_noncommercial=False)

    def busy(self):
        return self.preparing or any(not future.done() for future in self.futures.values())

    def available(self):
        if self.maintenance:
            raise HTTPException(409, 'מתבצע עדכון של היישום. פתחו אותו שוב לאחר סיום העדכון.')

    @staticmethod
    def write(path: Path, data: dict):
        staged = path.with_suffix(".tmp")
        staged.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        os.replace(staged, path)

    def folder(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", project_id):
            raise HTTPException(404, "הפרויקט לא נמצא")
        folder = self.root / project_id
        if not (folder / "project.json").is_file():
            raise HTTPException(404, "הפרויקט לא נמצא")
        return folder

    def load(self, project_id: str) -> dict:
        with self.lock:
            try:
                data = self.read_project(self.folder(project_id) / 'project.json')
            except (ValueError, OSError) as error:
                raise HTTPException(409, 'קובץ הפרויקט פגום. הקבצים המקוריים נשמרו לשחזור.') from error
            data.setdefault("speech_model", AUTO_MODEL)
            data.setdefault("segments", [])
            return data

    def update(self, project_id: str, **changes):
        with self.lock:
            data = self.load(project_id)
            data["settings"] = Settings(**data["settings"]).model_dump()
            data.update(changes, updated=time.time())
            self.write(self.folder(project_id) / "project.json", data)
            return data

    def idle(self, project_id: str):
        self.available()
        data = self.load(project_id)
        if data["state"] in BUSY:
            raise HTTPException(409, "יש פעולה בתהליך. יש להמתין לסיום.")
        return data

    def report(self, project_id: str) -> dict:
        path = self.folder(project_id) / "timings.json"
        if not path.exists():
            raise HTTPException(409, "קודם צריך להתאים את המילים לווידאו")
        with self.lock:
            report = json.loads(path.read_text(encoding="utf-8"))
            # Upgrade existing projects locally too: a single missing interval
            # must not hide every caption or require a fresh model download.
            if timing_problems(report):
                data = self.load(project_id)
                if report["transcript"] == data["transcript"]:
                    validate_report(report, Transcript(data["transcript"]), data["fingerprint"])
                    before = copy.deepcopy(report)
                    model = (data.get("model_selection") or {}).get("model", DEFAULT_MODEL)
                    cache = path.parent / evidence_cache_name(model)
                    evidence = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
                    complete_timings(report, evidence if evidence and evidence.get("media_sha256") == data["fingerprint"] else None)
                    if report != before:
                        backup = path.with_name("timings.before-automatic.json")
                        if not backup.exists():
                            self.write(backup, before)
                        self.write(path, report)
                        self.update(project_id, export_valid=False)
            return refresh_checks(report)

    def snapshot(self, project_id: str) -> dict:
        with self.lock:
            data = self.load(project_id)
            data["settings"] = Settings(**data["settings"]).model_dump()
            path = self.folder(project_id) / "timings.json"
            data['export_options'] = [dict(value=value, width=size[0], height=size[1],
                upscaled=min(size) > min(data['width'], data['height']))
                for value in media.EXPORT_RESOLUTIONS
                for size in [media.export_dimensions(data['width'], data['height'], value)]]
            data["report"] = self.report(project_id) if path.exists() else None
            data["export_valid"] = self.load(project_id).get("export_valid", False)
            if data["report"] and data["report"]["transcript"] != data["transcript"]:
                data["report"] = None  # Never display an old source while a new source is aligning.
            data["problems"] = timing_problems(data["report"]) if data["report"] else []
            data["checks_needed"] = verification_problems(data["report"]) if data["report"] else []
            data["lines"] = []
            block_mode = data["settings"]["caption_mode"] == "segments" and bool(data["segments"])
            if block_mode:
                validate_segments(data["segments"], data["transcript"], data["duration"])
                data["problems"] = []
            if block_mode or data["report"] and not data["problems"]:
                style = style_from(data["settings"])
                lines = segment_lines(data["segments"]) if block_mode else group_words(report_words(data["report"]),
                                    max_words=data["settings"]["words_per_line"], snap_gaps=False, fixed_count=True)
                offset = 0
                display_indexes = [row["index"] for row in display_rows(data["report"])] if not block_mode else []
                for line in lines:
                    data["lines"].append(dict(start=line.start, end=line.end,
                                              indexes=(list(range(offset, offset + len(line.words))) if block_mode else
                                                       display_indexes[offset:offset + len(line.words)]),
                                              font_size=_fit_size(line, style, data["width"], data["height"])))
                    offset += len(line.words)
            data["export_ready"] = ((self.folder(project_id) / "captioned.mp4").exists()
                                    and data.get("export_valid", False)
                                    and not data["problems"]
                                    and data.get("motion_revision") == MOTION_REVISION)
            return data

    def create(self, source, name: str) -> dict:
        self.available()
        project_id = uuid.uuid4().hex
        folder = self.root / project_id
        folder.mkdir()
        video = folder / "video.mp4"
        total = 0
        try:
            with video.open('wb') as output:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > 2 * 1024**3:
                        raise HTTPException(413, 'גודל הקובץ המרבי הוא 2GB')
                    if shutil.disk_usage(folder).free < len(chunk) + 64 * 1024**2:
                        raise OSError(errno.ENOSPC, 'Insufficient disk space')
                    output.write(chunk)
            try:
                size = media.probe_video_size(video)
                duration = media.probe_duration(video)
                if not size or duration <= 0:
                    raise ValueError('No video')
            except media.FFmpegNotFound as error:
                raise HTTPException(503, 'רכיב הווידאו חסר. פתחו את הכנת המחשב ונסו שוב.') from error
            except Exception as error:
                raise HTTPException(422, 'לא ניתן לקרוא את הווידאו. בחרו קובץ MP4 תקין.') from error
            data = dict(id=project_id, name=Path(name.replace('\\', '/')).name,
                        width=size[0], height=size[1], duration=duration, bytes=total,
                        fingerprint=media_digest(video), transcript='', mode='exact',
                        speech_model=AUTO_MODEL, settings=Settings().model_dump(), state='ready',
                        progress=0, message='', created=time.time(), updated=time.time(), export_valid=False)
            self.write(folder / 'project.json', data)
            return self.snapshot(project_id)
        except BaseException:
            # Only this call's new UUID directory can be removed; never an existing project.
            if folder.resolve().parent == self.root.resolve():
                shutil.rmtree(folder, ignore_errors=True)
            raise

    def submit(self, project_id: str, state: str, job, **changes):
        with self.lock:
            self.idle(project_id)
            if self.busy():
                raise HTTPException(409, "מתבצעת כרגע פעולה בפרויקט אחר. נסו שוב בסיום.")
            self.update(project_id, state=state, progress=0, message="", **changes)
            self.futures[project_id] = self.executor.submit(self.run, project_id, job)

    def run(self, project_id: str, job):
        try:
            job()
        except SpeechModelError as error:
            logging.exception("Speech model failed: %s", project_id)
            self.update(project_id, state="error", message=str(error), progress=0)
        except Exception:
            logging.exception("Project operation failed: %s", project_id)
            self.update(project_id, state="error", message="הפעולה לא הושלמה. אפשר לנסות שוב; התמלול נשמר.", progress=0)

    def selected_operation(self, project_id: str, operation, plan=None):
        plan = plan or resolve_selection(self.load(project_id)["speech_model"])
        def selected(selection):
            self.update(project_id, model_selection=selection,
                        message=("נבחר אוטומטית: " if selection["automatic"] else "נבחר: ") +
                        model_label(selection["model"]) + ". " + selection["reason"])
        return run_selected(plan, operation, selected)

    def align(self, project_id: str):
        data = self.load(project_id)
        folder = self.folder(project_id)
        video = folder / "video.mp4"
        plan = resolve_selection(data["speech_model"])
        self.update(project_id, model_selection=plan)
        if data["mode"] == "exact":
            source = Transcript(data["transcript"])
        else:
            words = self.selected_operation(project_id, lambda name, device:
                transcribe(video, language="he", model_name=name, device=device,
                           progress=lambda message: self.update(project_id, message=message)), plan)
            if not words:
                raise ValueError("No speech detected")
            source = Transcript(" ".join(w.text for w in words))
            self.update(project_id, transcript=source.text)
            plan = self.load(project_id)["model_selection"]
        self.update(project_id, message="מתאימים את ההתחלה והסיום של כל מילה לקול בעברית")
        try:
            # Long recordings need local speech anchors first. Verification below
            # locates them and aligns bounded source windows, including sung audio.
            report = (new_report(source, data["duration"], data["fingerprint"])
                      if data['mode'] == 'exact' and data['duration'] > 30 else
                      align_transcript(video, source, device="cuda" if plan["device"] == "cuda" else "cpu",
                                       **({'guidance': [vars(w) for w in words]} if data['mode'] == 'auto' else {})))
            message = ""
        except Exception as error:
            logging.exception("Alignment failed, retaining all source words")
            report = new_report(source, data["duration"], data["fingerprint"])
            for word in report["words"]:
                word["reason"] = f"Alignment failed: {error}"
            message = "לא הצלחנו להשלים התאמה אוטומטית. כל המילים נשמרו וניתן לתזמן אותן ידנית."
        if data["mode"] == "auto":
            report["transcription_model"] = plan["model"]
            # This is ASR -> acoustic alignment, not a second independent recognition.
            evidence = build_evidence(video, words, plan["model"], aligned=report)
            self.write(folder / evidence_cache_name(plan["model"]), evidence)
            complete_timings(report, evidence)
        else:
            complete_timings(report)
        with self.lock:
            save_report(folder / "timings.json", report)
            will_verify = bool(report["words"])
            self.update(project_id, state="verifying" if will_verify else "review",
                        progress=1, message=message, export_valid=False)
        if will_verify:
            self.verify(project_id, plan)

    def verify(self, project_id: str, plan=None):
        folder = self.folder(project_id)
        report = self.report(project_id)
        self.write(folder / f"timings.before-refinement-{time.time_ns()}.json", report)
        # Recover provenance for the old UI's automatic gap fill. Only an exact
        # match to the saved pre-fill report qualifies; real edits stay manual.
        baseline = folder / 'timings.before-gap-fill.json'
        if baseline.exists():
            from .automatic_timing import recover_gap_provenance
            recover_gap_provenance(report, json.loads(baseline.read_text(encoding='utf-8')))
        self.update(project_id, state="verifying", message="מקשיבים ובודקים את זמן ההופעה של כל מילה", progress=0)
        try:
            self.selected_operation(project_id, lambda name, device:
                verify_report(folder / "video.mp4", report, folder / evidence_cache_name(name),
                              model_name=name, device=device, auto_timing=True,
                              resolve_manual_conflicts=self.load(project_id).get('resolve_manual_conflicts', False),
                              include_noncommercial=self.preferences()['include_noncommercial'],
                              progress=lambda message: self.update(project_id, message=message)), plan)
            estimates = sum(row.get('status') == 'estimated' for row in report['words'])
            message = (f"התזמון חושב מחדש לפי הקול. {estimates} מילים עדיין בתזמון משוער." if estimates else
                       "כל המילים קיבלו התאמה קולית. התזמונים הקודמים נשמרו בגיבוי.")
            if report.get('singing_recovery') and report.get('full_audit'):
                supported=report['full_audit']['counts'].get('supported',0)
                message=f"כל {len(report['words'])} המילים תוזמנו אוטומטית. {supported} תזמונים נתמכים בבדיקות חוזרות; נשמר גיבוי."
        except Exception as error:
            logging.exception("Independent speech verification failed")
            for row in report["words"]:
                check = row.get("check", {})
                if not (check.get("status") == "listened" and check.get("confirmed_start") == row.get("start")
                        and check.get("confirmed_end") == row.get("end")):
                    row["check"] = dict(status="uncertain", reason="unavailable", suggested=None)
            model_name = (self.load(project_id).get("model_selection") or {}).get("model", DEFAULT_MODEL)
            report["verification"] = dict(model=model_name, status="failed", checked_at=time.time())
            report["verification"]["detail"] = str(error)
            message = "הכתוביות מוכנות עם תזמון משוער. אפשר לנגן, לעצב ולהוריד."
        complete_timings(report)
        with self.lock:
            save_report(folder / "timings.json", report)
            self.update(project_id, state="review", progress=1, message=message, export_valid=False)

    def subtitles(self, project_id: str):
        data = self.load(project_id)
        if data["settings"].get("caption_mode") == "segments":
            validate_segments(data["segments"], data["transcript"], data["duration"])
            lines = segment_lines(data["segments"])
            return data, lines, build_ass(lines, style_from(data["settings"]), data["width"], data["height"])
        report = self.report(project_id)
        validate_report(report, Transcript(data["transcript"]), data["fingerprint"])
        words = report_words(report)
        lines = group_words(words, max_words=data["settings"]["words_per_line"], snap_gaps=False, fixed_count=True)
        return data, lines, build_ass(lines, style_from(data["settings"]), data["width"], data["height"])

    def export(self, project_id: str):
        data, lines, ass = self.subtitles(project_id)
        folder = self.folder(project_id)
        if media_digest(folder / "video.mp4") != data["fingerprint"]:
            raise ValueError("Source video changed")
        (folder / "captions.ass").write_text(ass, encoding="utf-8")
        (folder / "captions.srt").write_text(build_srt(lines, data["settings"]["highlight_color"],
                                                      data["settings"].get("word_animation", True)), encoding="utf-8")
        media.burn_subtitles(folder / "video.mp4", folder / "captions.ass",
                             folder / "captioned.mp4", FONTS_DIR,
                             output_size=(None if data['settings'].get('export_resolution', 'source') == 'source' else
                                 media.export_dimensions(data['width'], data['height'], data['settings']['export_resolution'])),
                             progress=lambda value: self.update(project_id, progress=value))
        self.update(project_id, state="done", progress=1, export_valid=True,
                    motion_revision=MOTION_REVISION, message="הסרטון מוכן")


def create_app(data_dir: Path | None = None) -> FastAPI:
    studio = Studio(data_dir or project_dir())
    preparation = Preparation()

    @asynccontextmanager
    async def lifespan(app):
        yield
        studio.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="כתוב · Local subtitle studio", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.studio = studio

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"detail": "Local access only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Cross-origin access is disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
            "font-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(OSError)
    async def storage_error(request, error):
        logging.error('Local storage error: %s', error)
        if error.errno == errno.ENOSPC or getattr(error, 'winerror', None) == 112:
            return JSONResponse({'detail': 'אין מספיק מקום פנוי. פנו מקום בכונן ונסו שוב.'}, status_code=507)
        return JSONResponse({'detail': 'לא ניתן לקרוא או לשמור קובץ. בדקו הרשאות ומקום פנוי ונסו שוב.'}, status_code=503)

    @app.get("/api/health")
    def health():
        with studio.lock:
            return dict(app='katuv', version=2, local=True, device='computer',
                        app_version=__version__, installation_id=studio.installation_id,
                        runtime_id=Path(sys.prefix).name, busy=studio.busy(), maintenance=studio.maintenance)

    @app.post('/api/system/maintenance')
    def maintenance(body: MaintenanceInput):
        with studio.lock:
            if body.enabled and studio.busy():
                raise HTTPException(409, 'יש עיבוד או הכנת מודלים פעילים. המתינו לסיום.')
            studio.maintenance = body.enabled
            return health()

    @app.put('/api/system/preferences')
    def preferences(body: PrepareInput):
        with studio.lock:
            studio.available()
            if studio.busy():
                raise HTTPException(409, 'המתינו לסיום העיבוד לפני שינוי הגדרות המודלים.')
            studio.write(studio.root / '.preferences.json', body.model_dump())
            return studio.preferences()

    @app.get('/api/system')
    def system_status():
        return dict(diagnostics(), preparation=preparation.snapshot(), preferences=studio.preferences())

    @app.get('/api/system/preparation')
    def preparation_status():
        return preparation.snapshot()

    @app.post('/api/system/prepare')
    def prepare_system(body: PrepareInput):
        return preparation.start(studio, body.include_noncommercial)

    @app.get("/api/motion")
    def motion():
        return {name: motion_spec(style) for name, style in PRESETS.items()}

    @app.get("/api/models")
    def models():
        return {"default": AUTO_MODEL, "models": model_catalog(), "automatic": resolve_selection(AUTO_MODEL)}

    @app.get("/api/projects")
    def projects():
        with studio.lock:
            return studio.projects()

    @app.post("/api/projects")
    def upload(video: UploadFile):
        if not video.filename or Path(video.filename).suffix.lower() != ".mp4":
            raise HTTPException(422, "יש לבחור קובץ MP4")
        try:
            with studio.lock:
                return studio.create(video.file, video.filename)
        finally:
            video.file.close()

    @app.post("/api/demo")
    def demo():
        with studio.lock:
            sample = Path(__file__).with_name('assets') / 'hebrew-source.mp4'
            report_path = sample.with_suffix(".timings.json")
            if not sample.exists() or not report_path.exists():
                raise HTTPException(404, "קובץ הדוגמה לא מותקן. אפשר לבחור סרטון משלכם.")
            with sample.open("rb") as source:
                data = studio.create(source, "סרטון לדוגמה.mp4")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            # Demonstration reuses the verified real Hebrew alignment.
            validate_report(report, Transcript(report["transcript"]), data["fingerprint"])
            save_report(studio.folder(data["id"]) / "timings.json", report)
            studio.update(data["id"], transcript=report["transcript"], state="review")
            return studio.snapshot(data["id"])


    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return studio.snapshot(project_id)

    @app.post("/api/projects/{project_id}/align", status_code=202)
    def align(project_id: str, body: AlignInput):
        if body.mode == "exact":
            Transcript(body.transcript)  # empty exact mode must never fall back to ASR
        with studio.lock:
            data = studio.idle(project_id)
            studio.submit(project_id, "aligning", lambda: studio.align(project_id),
                          transcript=body.transcript, mode=body.mode, export_valid=False,
                          segments=data["segments"] if body.mode == "exact" and body.transcript == data["transcript"] else [],
                          settings=dict(data["settings"], caption_mode="words"))
        return {"state": "aligning"}

    @app.put("/api/projects/{project_id}/settings")
    def settings(project_id: str, body: Settings):
        with studio.lock:
            data = studio.idle(project_id)
            if body.caption_mode == "segments":
                validate_segments(data["segments"], data["transcript"], data["duration"])
            studio.update(project_id, settings=body.model_dump(), export_valid=False)
            return studio.snapshot(project_id)

    @app.post("/api/projects/{project_id}/segments/prepare")
    def prepare_passages(project_id: str, body: AlignInput):
        with studio.lock:
            data = studio.idle(project_id)
            layout = body.transcript or data["transcript"]
            source = Transcript(layout)
            if data["transcript"] and list(source.tokens) != list(Transcript(data["transcript"]).tokens):
                raise ValueError("החלוקה צריכה לכלול את כל המילים המקוריות ובאותו הסדר.")
            report_path = studio.folder(project_id)/"timings.json"
            report = studio.report(project_id) if report_path.exists() else None
            cues = data["segments"] if data["segments"] and not body.transcript else prepare_segments(
                layout, data["duration"], report, data["segments"])
            validate_segments(cues, data["transcript"] or layout, data["duration"])
            studio.update(project_id, segments=cues, transcript=data["transcript"] or layout,
                          mode="exact", state="review", export_valid=False,
                          settings=dict(data["settings"], caption_mode="segments"))
            return studio.snapshot(project_id)

    @app.put("/api/projects/{project_id}/segments")
    def save_passages(project_id: str, body: SegmentsInput):
        with studio.lock:
            data = studio.idle(project_id)
            cues = [cue.model_dump() for cue in body.segments]
            validate_segments(cues, data["transcript"], data["duration"])
            studio.update(project_id, segments=cues, export_valid=False, state="review")
            return studio.snapshot(project_id)

    @app.put("/api/projects/{project_id}/model")
    def speech_model(project_id: str, body: ModelInput):
        with studio.lock:
            studio.idle(project_id)
            # A selection configures the NEXT run. Existing evidence retains its
            # actual model attribution and manual timings remain untouched.
            studio.update(project_id, speech_model=body.speech_model, model_selection=None)
            return studio.snapshot(project_id)

    @app.post("/api/projects/{project_id}/verify", status_code=202)
    def verify(project_id: str, body: VerifyInput | None = None):
        with studio.lock:
            data = studio.idle(project_id)
            report = studio.report(project_id)
            validate_report(report, Transcript(data["transcript"]), data["fingerprint"])
            if body and body.resolve_manual_conflicts is not None:
                studio.update(project_id, resolve_manual_conflicts=body.resolve_manual_conflicts)
            studio.submit(project_id, "verifying", lambda: studio.verify(project_id), export_valid=False)
        return {"state": "verifying"}

    @app.patch("/api/projects/{project_id}/timings")
    def timings(project_id: str, body: TimingInput):
        with studio.lock:
            data = studio.idle(project_id)
            report = studio.report(project_id)
            validate_report(report, Transcript(data["transcript"]), data["fingerprint"])
            indexes = [word.index for word in body.words]
            if len(set(indexes)) != len(indexes) or any(i >= len(report["words"]) for i in indexes):
                raise HTTPException(422, "מזהה מילה לא תקין")
            for word in body.words:
                row = report["words"][word.index]
                if body.edit_kind != 'manual':
                    ordered = display_rows(report)
                    position = next(i for i, item in enumerate(ordered) if item['index'] == word.index)
                    if body.edit_kind == 'gap_fill':
                        valid = (valid_interval(word.start, word.end, report['duration'])
                                 and position + 1 < len(ordered) and word.start == row['start']
                                 and abs(word.end - ordered[position + 1]['start']) < 1e-7 and word.end >= row['end'])
                        if valid:
                            row.setdefault('gap_fill', dict(end=row['end']))
                            word.end = ordered[position + 1]['start']
                    else:
                        valid = word.start == row['start'] and word.end == row.get('gap_fill', {}).get('end')
                        if valid:
                            row.pop('gap_fill', None)
                    if not valid:
                        raise HTTPException(422, 'השלמת רווחים יכולה לשנות רק את סוף התצוגה עד המילה הבאה')
                    row['end'] = word.end
                    continue
                row.pop('gap_fill', None)
                row.update(start=word.start, end=word.end, status="manual", reason="")
                row['manual_timing'] = True
                row["check"] = dict(status="listened" if word.listened else "uncertain",
                                    reason="user_listened" if word.listened else "timing_changed", suggested=None,
                                    confirmed_start=word.start if word.listened else None,
                                    confirmed_end=word.end if word.listened else None)
            if body.allow_reorder:
                report["allow_reordered_timings"] = True
            if timing_problems(report):
                raise HTTPException(422, "יש לבחור זמנים בתוך הסרטון, לפי סדר המילים וללא חפיפה.")
            save_report(studio.folder(project_id) / "timings.json", report)
            studio.update(project_id, state="review", export_valid=False)
            return studio.snapshot(project_id)

    @app.post("/api/projects/{project_id}/export", status_code=202)
    def export(project_id: str):
        with studio.lock:
            studio.idle(project_id)
            studio.subtitles(project_id)  # validate before creating any job or output
            studio.submit(project_id, "exporting", lambda: studio.export(project_id), export_valid=False)
        return {"state": "exporting"}

    @app.get("/api/projects/{project_id}/waveform")
    def waveform(project_id: str, start: float | None = None, end: float | None = None):
        import numpy as np
        folder = studio.folder(project_id)
        path = folder / "waveform.json"
        duration = studio.load(project_id)["duration"]
        windowed = start is not None or end is not None
        if windowed and (start is None or end is None or not math.isfinite(start) or not math.isfinite(end)
                         or not 0 <= start < end <= duration or end-start > 10):
            raise HTTPException(422, "בחרו טווח בתוך הסרטון, באורך של עד 10 שניות")
        if not windowed and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        start, end = (start, end) if windowed else (0, duration)
        pcm = folder / "waveform-v1.f32"
        with studio.lock:
            if not pcm.exists():
                staged = pcm.with_suffix(".tmp")
                staged.write_bytes(media.extract_audio(folder / "video.mp4"))
                os.replace(staged, pcm)
        if not pcm.stat().st_size:
            raise HTTPException(422, "אין קול זמין בסרטון הזה")
        samples = np.memmap(pcm, mode="r", dtype=np.float32)
        try:
            count = 600
            # Include trailing video silence; absolute times match the video ruler.
            bins = np.zeros(count)
            for i in range(count):
                chunk = samples[int((start+i*(end-start)/count)*16000):int((start+(i+1)*(end-start)/count)*16000)]
                if chunk.size:
                    bins[i] = np.sqrt(np.mean(chunk.astype(np.float64) ** 2))
            peak = max(float(bins.max()), .001)
            values = [round(float(value / peak), 3) for value in bins]
        finally:
            del samples
        if not windowed:
            studio.write(path, values)
        return values

    @app.get("/api/projects/{project_id}/preview")
    def preview(project_id: str, at: float = 0):
        data, _, ass = studio.subtitles(project_id)
        if not 0 <= at < data["duration"]:
            raise HTTPException(422, "זמן התצוגה מחוץ לווידאו")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="katuv-frame-") as temp:
            ass_path = Path(temp) / "frame.ass"
            ass_path.write_text(ass, encoding="utf-8")
            with media.subtitle_workspace(ass_path, FONTS_DIR) as (cwd, vf):
                resolution = data['settings'].get('export_resolution', 'source')
                vf = media.subtitle_filter(vf, None if resolution == 'source' else
                    media.export_dimensions(data['width'], data['height'], resolution))
                result = subprocess.run(
                    [media.find_tool("ffmpeg"), "-v", "error", "-i", str(studio.folder(project_id) / "video.mp4"),
                     "-vf", vf, "-ss", str(at), "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"],
                    cwd=cwd, capture_output=True, check=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return Response(result.stdout, media_type="image/png")

    @app.get("/api/projects/{project_id}/file/{kind}")
    def file(project_id: str, kind: Literal["video", "export", "ass", "srt", "report"]):
        data = studio.load(project_id)
        filenames = {"video": "video.mp4", "export": "captioned.mp4", "ass": "captions.ass",
                     "srt": "captions.srt", "report": "timings.json"}
        if kind in ("export", "ass", "srt") and (not data.get("export_valid")
                                                 or data.get("motion_revision") != MOTION_REVISION):
            raise HTTPException(409, "יש לייצא מחדש לאחר שינוי העיצוב או התזמונים")
        path = studio.folder(project_id) / filenames[kind]
        if not path.exists():
            raise HTTPException(404, "הקובץ עדיין לא מוכן")
        if kind == "video":
            return FileResponse(path, media_type="video/mp4")  # byte ranges support scrubbing
        suffix = path.suffix
        return FileResponse(path, filename=Path(data["name"]).stem + (".captioned" if kind == "export" else "") + suffix)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")
    return app


def main():
    parser = argparse.ArgumentParser(description="Open the local Hebrew subtitle studio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the studio in your browser")
    args = parser.parse_args()
    import urllib.request
    import webbrowser
    import socket
    url = f"http://127.0.0.1:{args.port}"
    data_dir = project_dir()
    running = None
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            running = json.load(response)
    except (OSError, ValueError):
        pass
    if running is not None:
        if not isinstance(running, dict) or any(running.get(key) != value for key, value in {
                'app': 'katuv', 'app_version': __version__,
                'installation_id': installation_identity(data_dir), 'runtime_id': Path(sys.prefix).name}.items()):
            raise SystemExit(f'הפורט {args.port} תפוס על ידי יישום או התקנה אחרים. סגרו אותם או בחרו --port אחר.')
        if running.get('maintenance'):
            raise SystemExit('היישום מתעדכן כעת. פתחו אותו לאחר סיום העדכון.')
        if args.open:
            webbrowser.open(url)
        return
    with socket.socket() as probe:
        probe.settimeout(.5)
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise SystemExit(f'הפורט {args.port} תפוס. סגרו את היישום המשתמש בו או בחרו --port אחר.')
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data_dir / "studio.log", level=logging.INFO,
                        encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    import uvicorn
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(data_dir), host="127.0.0.1", port=args.port, log_config=None, proxy_headers=False)


if __name__ == "__main__":
    main()
