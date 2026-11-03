"""Local resource-aware Hebrew model selection; thresholds are conservative heuristics."""
from __future__ import annotations

import os

from .models import BY_ID, IVRIT_LARGE, IVRIT_TURBO, SpeechModelError, model_label

AUTO_MODEL = "auto"
GIB = 1024 ** 3


def probe_hardware() -> dict:
    import psutil
    from .transcribe import _cuda_usable

    memory = psutil.virtual_memory()
    cores = psutil.cpu_count(logical=False) or max(1, (os.cpu_count() or 2) // 2)
    result = dict(ram_total_gb=round(memory.total/GIB, 1), ram_available_gb=round(memory.available/GIB, 1),
                  cpu_cores=cores, cuda=False, gpu_name=None, vram_total_gb=0, vram_available_gb=0)
    if _cuda_usable():
        try:
            import torch
            import ctranslate2
            free, total = torch.cuda.mem_get_info(0)
            # Detect usable inference, not just a listed GPU or installed driver.
            if "float16" in ctranslate2.get_supported_compute_types("cuda", 0):
                result.update(cuda=True, gpu_name=torch.cuda.get_device_name(0),
                              vram_total_gb=round(total/GIB, 1), vram_available_gb=round(free/GIB, 1))
        except Exception:
            pass  # Unknown accelerator resources use the conservative CPU plan.
    return result


def choose_model(hardware: dict) -> dict:
    ram = max(0, hardware.get("ram_available_gb", 0))
    cores = max(1, hardware.get("cpu_cores", 1))
    cuda = bool(hardware.get("cuda"))
    vram = max(0, hardware.get("vram_available_gb", 0)) if cuda else 0
    device = "cpu"
    if ram >= 6 and vram >= 7:
        model, device = IVRIT_LARGE, "cuda"
    elif ram >= 4 and vram >= 4:
        model, device = IVRIT_TURBO, "cuda"
    elif ram >= 12 and cores >= 6:
        model = IVRIT_LARGE
    elif ram >= 6 and cores >= 4:
        model = IVRIT_TURBO
    elif ram >= 2.5:
        model = "small"
    else:
        model = "base"
    if model == IVRIT_LARGE:
        reason = "יש מספיק משאבים למודל העברי הגדול. נותנים עדיפות לאיכות; העיבוד עשוי לקחת זמן."
    elif model == IVRIT_TURBO:
        reason = "המודל העברי הקל יותר מתאים למשאבים הזמינים ומשאיר זיכרון פנוי לעבודה."
    else:
        reason = "נבחר מודל רב־לשוני קל שתומך בעברית, כדי להתאים לזיכרון הפנוי כרגע."
    return dict(model=model, device=device, automatic=True, reason=reason, hardware=hardware)


def resolve_selection(selection: str, hardware: dict | None = None) -> dict:
    hardware = probe_hardware() if hardware is None else hardware
    if selection == AUTO_MODEL:
        return choose_model(hardware)
    if selection not in BY_ID:
        raise ValueError("בחרו מודל תומך עברית מהרשימה")
    return dict(model=selection, device="auto", automatic=False, reason="בחירה ידנית", hardware=hardware)


def resource_failure(error: BaseException) -> bool:
    """Only memory exhaustion permits an automatic model downgrade, never network errors."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, MemoryError) or any(term in str(error).lower() for term in (
            "out of memory", "bad allocation", "bad_alloc", "cannot allocate memory", "not enough memory")):
            return True
        error = error.__cause__ or error.__context__
    return False


def run_selected(plan: dict, operation, on_selection):
    """Pin a concrete model for each operation and expose any fallback before running it."""
    candidates = [IVRIT_LARGE, IVRIT_TURBO, "small", "base"]
    current = dict(plan)
    while True:
        on_selection(current)
        try:
            return operation(current["model"], current["device"])
        except SpeechModelError as error:
            name = current["model"]
            if not current["automatic"] or not resource_failure(error) or name not in candidates[:-1]:
                raise
            next_model = candidates[candidates.index(name)+1]
            current = dict(current, model=next_model, fallback_from=plan["model"],
                           reason="לא היה מספיק זיכרון בזמן ההרצה. עברנו אוטומטית ל־" + model_label(next_model))
