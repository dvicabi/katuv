import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required to test the browser's motion math")
def test_browser_motion_contract():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([shutil.which("node"), str(root/"tests/test_motion.cjs")], cwd=root,
                   env={**os.environ,"AUTOCAPTION_TEST_PYTHON":sys.executable}, check=True,
                   capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
def test_caption_visibility_with_missing_and_estimated_timings():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([shutil.which("node"), str(root/"tests/test_timeline.cjs")],
                   cwd=root, check=True, capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
def test_timecode_inputs_precision_and_playback_shortcuts():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([shutil.which("node"), str(root/"tests/test_timecode.cjs")],
                   cwd=root, check=True, capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
def test_word_timeline_swaps_groups_and_history():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([shutil.which("node"), str(root/"tests/test_word_timeline.cjs")],
                   cwd=root, check=True, capture_output=True, text=True)


@pytest.mark.skipif(not shutil.which("node"), reason="Node is required")
def test_hebrew_keyboard_shortcuts_and_history_order():
    root = Path(__file__).resolve().parents[1]
    subprocess.run([shutil.which("node"), str(root/"tests/test_shortcuts.cjs")],
                   cwd=root, check=True, capture_output=True, text=True)
