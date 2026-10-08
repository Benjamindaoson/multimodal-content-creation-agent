"""Deterministic, vendor-independent tests for repurposing correctness."""

import pytest

from app.engine.agents.workflow.multimodal_repurposing import (
    TranscriptSegment,
    format_window,
    normalize_segments,
    transcript_windows,
    validated_candidates,
)


def test_provider_segment_offsets_and_invalid_timestamps():
    segments = normalize_segments(
        [
            {"start": 0, "end": 4, "text": "  Hello    world! "},
            {"start": 9, "end": 5, "text": "backwards"},
            {"start": "nan", "end": 15, "text": "invalid"},
            {"start": 5, "end": 7, "text": "Next topic"},
        ],
        offset=600,
    )
    assert [(s.start, s.end, s.text) for s in segments] == [
        (600, 604, "Hello world!"),
        (605, 607, "Next topic"),
    ]


def test_windows_overlap_and_terminate_on_long_segments():
    segments = [
        TranscriptSegment(i * 10, i * 10 + 9, "description " + "x" * 80)
        for i in range(12)
    ]
    windows = transcript_windows(segments, max_chars=280, overlap_seconds=15)
    assert len(windows) > 1
    assert windows[0][-1] in windows[1]
    assert windows[-1][-1] == segments[-1]
    assert format_window(windows[0]).startswith("[0.000-9.000]")


def test_candidate_validation_requires_real_speech_and_duration():
    segments = [
        TranscriptSegment(10, 30, "The launch starts with a detailed explanation"),
        TranscriptSegment(31, 55, "A useful real demonstration and conclusion"),
    ]
    accepted = validated_candidates(
        [
            {"start": 10, "end": 50, "score": 0.7, "title": "Launch"},
            {"start": 11, "end": 49, "score": 0.9, "title": "Better"},
            {"start": 75, "end": 95, "score": 1.0, "title": "Invented"},
            {"start": 10, "end": 11, "title": "Too short"},
            {"start": -1, "end": 40, "title": "Negative"},
        ],
        segments,
        duration=100,
    )
    assert len(accepted) == 1
    assert accepted[0]["title"] == "Better"
    assert accepted[0]["evidence_excerpt"].startswith("The launch")


def test_invalid_window_configuration():
    with pytest.raises(ValueError):
        transcript_windows([], max_chars=5)
