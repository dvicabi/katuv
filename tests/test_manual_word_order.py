import copy

import pytest

from autocaption.alignment import (Transcript, new_report, timing_problems, report_words, validate_report)
from autocaption.automatic_timing import complete_timings


def test_manual_order_requires_explicit_opt_in_and_keeps_source_rows():
    source = Transcript('שלום, Reels 2026 שלום!')
    report = new_report(source, 4, 'video')
    for i, row in enumerate(report['words']):
        row.update(start=i+.1, end=i+.6, status='manual')
    report['words'][0].update(start=2.1, end=2.6)
    report['words'][2].update(start=.1, end=.6)
    assert timing_problems(report)
    report['allow_reordered_timings'] = True
    validate_report(report, source, 'video')
    assert not timing_problems(report)
    assert [word.text for word in report_words(report)] == ['2026', 'Reels', 'שלום,', 'שלום!']
    before = copy.deepcopy(report)
    complete_timings(report)
    assert report == before
    assert [row['text'] for row in report['words']] == list(source.tokens)
    report['words'][1]['text'] = 'אחר'
    with pytest.raises(ValueError):
        validate_report(report, source, 'video')
