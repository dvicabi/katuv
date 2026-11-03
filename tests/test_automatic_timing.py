import copy
import random

import pytest

from autocaption.alignment import Transcript, new_report, timing_problems, validate_report, report_words
from autocaption.automatic_timing import complete_timings


def report(text="שלום, Reels 2026 היום", duration=4):
    return new_report(Transcript(text), duration, "video")


def anchor(data, index, start, end, status="aligned"):
    data["words"][index].update(start=start, end=end, status=status, score=.9)


def test_missing_words_get_ordered_estimates_and_original_source_survives():
    data = report("  שָׁלוֹם,\nReels 2026  היום!  ")
    source = Transcript(data["transcript"])
    anchor(data, 0, .234, .715)
    anchor(data, 3, 2.119, 2.9, "manual")
    before = copy.deepcopy(data)
    complete_timings(data)
    validate_report(data, source, "video")
    assert not timing_problems(data)
    assert data["words"][0] == before["words"][0]
    assert data["words"][3] == before["words"][3]
    assert all(w["status"] == "estimated" for w in data["words"][1:3])
    assert all(w["original_timing"]["start"] is None for w in data["words"][1:3])
    assert [w.text for w in report_words(data)] == list(source.tokens)
    completed = copy.deepcopy(data)
    assert complete_timings(data) == completed  # Reloading never drifts timings.


def test_no_anchors_fills_all_words_without_claiming_acoustic_certainty():
    data = complete_timings(report())
    assert not timing_problems(data)
    assert all(w["check"]["status"] == "uncertain" for w in data["words"])
    assert all(w["timing_method"] == "neighbor_estimate" for w in data["words"])


def test_recognized_occurrences_supply_timings_never_display_words():
    data = report("שלום, שלום! Reels 2026")
    evidence = dict(media_sha256="video", words=[
        dict(text=text, start=start, end=end, probability=.96, alignment_score=.8)
        for text, start, end in [("שלום",.2,.5),("שלום",.7,.9),("Reels",1,1.4),("הזיה",2,2.2)]])
    complete_timings(data, evidence)
    assert [w["text"] for w in data["words"]] == ["שלום,", "שלום!", "Reels", "2026"]
    assert [w["start"] for w in data["words"][:3]] == [.2,.7,1]
    assert not timing_problems(data)
    assert data["words"][3]["timing_method"] == "neighbor_estimate"
    with pytest.raises(ValueError, match="different video"):
        complete_timings(data, dict(evidence, media_sha256="other"))


def test_raw_asr_estimate_is_used_when_acoustic_refinement_failed():
    data = report("שלום")
    complete_timings(data, dict(media_sha256="video", words=[
        dict(text="שלום",start=None,end=None,raw_start=.4,raw_end=.8,probability=.9)]))
    assert data["words"][0]["start"] == .4
    assert data["words"][0]["timing_method"] == "recognizer_estimate"


def test_adjacent_millisecond_anchors_are_preserved_without_rounding_gaps():
    data = report()
    for i in range(4):
        anchor(data, i, .603+i*.5, .603+(i+1)*.5)
    before = copy.deepcopy(data)
    assert complete_timings(data) == before


def test_recognition_cannot_displace_acoustic_anchors_or_compress_a_later_verse():
    data = report("שלום היום שלום מחר")
    anchor(data, 0, .2, .6)
    anchor(data, 2, 2.2, 2.6)
    anchor(data, 3, 2.6, 3)
    before = copy.deepcopy(data)
    evidence = dict(media_sha256="video", words=[
        dict(text="שלום",start=2,end=2.1,probability=.9),
        dict(text="היום",start=2.4,end=2.8,probability=.9)])
    complete_timings(data,evidence)
    assert not timing_problems(data)
    assert all(data["words"][i] == before["words"][i] for i in (0,2,3))
    assert .6 <= data["words"][1]["start"] < data["words"][1]["end"] <= 2.2


@pytest.mark.parametrize("index", [0, 1, 3])
def test_edge_missing_words_and_conflicting_anchors_still_render_every_word(index):
    data = report()
    for i in range(4):
        if i != index:
            anchor(data, i, .2+i*.6, .8+i*.6)
    complete_timings(data)
    assert len(report_words(data)) == 4
    assert not timing_problems(data)


def test_random_partial_and_overlapping_alignments_remain_bounded_and_complete():
    rng = random.Random(51)
    for _ in range(200):
        data = report(" ".join(["מילה"]*30), 12)
        for i in range(30):
            if rng.random() < .65:
                start = rng.uniform(0, 11.5)
                anchor(data, i, start, start+.4)
        complete_timings(data)
        assert not timing_problems(data)
        assert len(report_words(data)) == 30


def test_unrenderably_short_video_reports_a_real_constraint():
    with pytest.raises(ValueError, match="ארוך מדי"):
        complete_timings(report(duration=.02))
