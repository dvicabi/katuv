from unittest.mock import Mock

import pytest

from autocaption.hardware import choose_model, resolve_selection, run_selected
from autocaption.models import IVRIT_LARGE, IVRIT_TURBO, SpeechModelError


@pytest.mark.parametrize("ram,cores,cuda,vram,expected,device", [
    (48, 6, False, 0, IVRIT_LARGE, "cpu"),  # This user's AMD workstation.
    (8, 6, False, 0, IVRIT_TURBO, "cpu"),
    (4, 6, False, 0, "small", "cpu"),
    (1, 6, False, 0, "base", "cpu"),
    (48, 2, False, 0, "small", "cpu"),
    (16, 8, False, 16, IVRIT_LARGE, "cpu"),  # Unsupported GPU must not count.
    (8, 4, True, 8, IVRIT_LARGE, "cuda"),
    (5, 4, True, 5, IVRIT_TURBO, "cuda"),
    (3, 4, True, 2, "small", "cpu"),
    (1, 4, True, 16, "base", "cpu"),  # GPU memory is not a substitute for RAM.
])
def test_selection_uses_available_resources_not_installed_ram(ram, cores, cuda, vram, expected, device):
    result = choose_model(dict(ram_total_gb=64, ram_available_gb=ram, cpu_cores=cores,
                               cuda=cuda, vram_available_gb=vram))
    assert (result["model"], result["device"]) == (expected, device)
    assert result["automatic"]


def test_unknown_resources_and_manual_choice():
    assert choose_model({})["model"] == "base"
    assert resolve_selection(IVRIT_TURBO, {})["model"] == IVRIT_TURBO
    assert not resolve_selection(IVRIT_TURBO, {})["automatic"]
    with pytest.raises(ValueError):
        resolve_selection("small.en", {})


def test_memory_fallback_is_bounded_explicit_and_does_not_change_manual_choice():
    plan = choose_model(dict(ram_available_gb=48, cpu_cores=6))
    error = SpeechModelError("המודל לא נטען")
    error.__cause__ = MemoryError("allocation failed")
    operation = Mock(side_effect=[error, "evidence"])
    selected = Mock()
    assert run_selected(plan, operation, selected) == "evidence"
    assert [call.args for call in operation.call_args_list] == [(IVRIT_LARGE, "cpu"), (IVRIT_TURBO, "cpu")]
    assert selected.call_args.args[0]["fallback_from"] == IVRIT_LARGE
    assert plan["model"] == IVRIT_LARGE
    with pytest.raises(SpeechModelError):
        run_selected(dict(plan, automatic=False), Mock(side_effect=error), Mock())
    network = Mock(side_effect=SpeechModelError("download failed"))
    with pytest.raises(SpeechModelError):
        run_selected(plan, network, Mock())
    assert network.call_count == 1
