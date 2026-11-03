"""Curated local CTranslate2 models with Hebrew support. No remote inference."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_MODEL = "small"
IVRIT_TURBO = "ivrit-ai/whisper-large-v3-turbo-ct2"
IVRIT_LARGE = "ivrit-ai/whisper-large-v3-ct2"


@dataclass(frozen=True)
class SpeechModel:
    id: str
    repo: str
    label: str
    description: str
    download: str
    hardware: str
    group: str = "multilingual"


MODELS = (
    SpeechModel(IVRIT_TURBO, IVRIT_TURBO, "ivrit.ai Turbo — מותאם לעברית",
                "אומן במיוחד על דיבור בעברית. נקודת פתיחה טובה לסרטונים שרוב הדיבור בהם בעברית.",
                "כ־1.6 GB", "פועל גם במעבד רגיל; ההרצה עשויה לקחת יותר מזמן הסרטון.", "hebrew"),
    SpeechModel(IVRIT_LARGE, IVRIT_LARGE, "ivrit.ai Large — עברית, מודל גדול",
                "גרסה גדולה שאומנה לעברית. אפשר לנסות כשמודל אחר מתקשה בהקלטה.",
                "כ־3.1 GB", "כבד ואיטי יותר. מתאים למחשב עם הרבה זיכרון פנוי.", "hebrew"),
    SpeechModel("base", "Systran/faster-whisper-base", "Whisper Base — קל ומהיר",
                "לניסיון מהיר על מחשב רגיל. בהקלטות קשות צפויות יותר טעויות.",
                "כ־150 MB", "צריכת זיכרון נמוכה יחסית."),
    SpeechModel("small", "Systran/faster-whisper-small", "Whisper Small — מאוזן",
                "מודל קל יחסית, עם איזון בין זמן ההמתנה לבין איכות הזיהוי בעברית.",
                "כ־490 MB", "בחירה נוחה להתחלה גם בלי כרטיס מסך ייעודי."),
    SpeechModel("medium", "Systran/faster-whisper-medium", "Whisper Medium — בינוני",
                "מודל רב־לשוני גדול יותר, כולל עברית. אפשרות נוספת להשוואה מול Small.",
                "כ־1.5 GB", "דורש יותר זיכרון וזמן עיבוד מ־Small."),
    SpeechModel("turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "Whisper Turbo — רב־לשוני",
                "גרסת Turbo של Whisper, עם תמיכה בעברית ובשפות נוספות.",
                "כ־1.6 GB", "חלופה מהירה יותר ל־Large v3, אך עדיין מודל גדול."),
    SpeechModel("large-v3", "Systran/faster-whisper-large-v3", "Whisper Large v3 — רב־לשוני גדול",
                "הגרסה הרב־לשונית הגדולה. עוד אפשרות להקלטות שקשה לזהות.",
                "כ־3.1 GB", "כבד ואיטי יותר. כרטיס NVIDIA נתמך יכול לקצר את ההמתנה."),
)
BY_ID = {model.id: model for model in MODELS}
# Retain existing CLI options; never permit .en, distil English models or arbitrary repositories.
LEGACY_REPOS = {name: "Systran/faster-whisper-" + name for name in ("tiny", "large-v1", "large-v2")}
ALIASES = {"large": "large-v3", "large-v3-turbo": "turbo"}
MULTILINGUAL_MODELS = set(BY_ID) | set(LEGACY_REPOS) | set(ALIASES)


def validate_model(name: str | None) -> None:
    if name is not None and name not in MULTILINGUAL_MODELS:
        raise ValueError("בחרו מודל תומך עברית מהרשימה. מודלים באנגלית בלבד (.en) ומאגרים מותאמים אישית אינם נתמכים.")


def model_repo(name: str) -> str:
    validate_model(name)
    name = ALIASES.get(name, name)
    return BY_ID[name].repo if name in BY_ID else LEGACY_REPOS[name]


def model_label(name: str) -> str:
    model = BY_ID.get(ALIASES.get(name, name))
    return model.label if model else name


def cached_model_path(name: str) -> str | None:
    """Inspect the local HF cache only; a partial download is not ready offline."""
    from huggingface_hub import try_to_load_from_cache

    repo = model_repo(name)
    paths = [try_to_load_from_cache(repo, filename) for filename in
             ("model.bin", "config.json", "tokenizer.json")]
    vocabulary = [try_to_load_from_cache(repo, filename) for filename in
                  ("vocabulary.json", "vocabulary.txt")]
    if all(isinstance(p, str) and Path(p).is_file() for p in paths) and any(
            isinstance(p, str) and Path(p).is_file() for p in vocabulary):
        return str(Path(paths[0]).parent)
    return None


def model_catalog() -> list[dict]:
    return [dict(asdict(model), cached=cached_model_path(model.id) is not None) for model in MODELS]


def prepare_model(name: str) -> str:
    cached = cached_model_path(name)
    if cached:
        return cached
    from huggingface_hub import snapshot_download

    # Single-worker downloads avoid HF's first-use symlink-probe race on Windows
    # without administrator rights. Complete caches never contact the network.
    return snapshot_download(model_repo(name), allow_patterns=[
        "config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"
    ], max_workers=1)


def evidence_cache_name(name: str) -> str:
    from hashlib import sha256

    # Keep the existing default-model cache useful for older projects.
    return "speech-evidence.json" if name == DEFAULT_MODEL else "speech-evidence-" + sha256(
        model_repo(name).encode()).hexdigest()[:16] + ".json"


class SpeechModelError(RuntimeError):
    pass
