import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from workers.stacktrace import (
    StackFrame,
    fingerprint,
    normalize_filename,
    normalize_frames,
    parse_stacktrace,
    select_relevant_frames,
)

FIXTURES = Path(__file__).parent / "fixtures" / "stack_traces.json"


def test_regression_corpus_parses_supported_runtimes() -> None:
    corpus = json.loads(FIXTURES.read_text())
    expected = {"chrome": 3, "firefox": 3, "safari": 2, "node": 4}
    for runtime, count in expected.items():
        assert len(parse_stacktrace(corpus[runtime])) == count


def test_malformed_and_incomplete_lines_are_ignored() -> None:
    corpus = json.loads(FIXTURES.read_text())
    frames = parse_stacktrace(corpus["malformed"])
    assert len(frames) == 1
    assert frames[0].filename == "/srv/app/index.js"
    assert frames[0].lineno == 12
    assert frames[0].colno is None


def test_filename_query_and_hash_do_not_change_grouping() -> None:
    first = parse_stacktrace("at render (https://app.test/app.js?v=1#one:10:2)")
    second = parse_stacktrace("at render (https://app.test/app.js?v=2#two:900:70)")
    assert fingerprint(first) == fingerprint(second)
    assert first[0].filename == "https://app.test/app.js"


def test_line_and_column_numbers_do_not_change_fingerprint() -> None:
    first = [StackFrame("/app/main.js", "render", 10, 2)]
    second = [StackFrame("/app/main.js", "render", 900, 70)]
    assert fingerprint(first) == fingerprint(second)


def test_in_app_frames_are_preferred_and_vendor_frames_are_removed() -> None:
    frames = [
        StackFrame("/app/main.js", "render", 10, 2, True),
        StackFrame("/app/node_modules/react/index.js", "dispatch", 20, 3, False),
        StackFrame("node:internal/process/task_queues", "run", 30, 4, False),
    ]
    assert select_relevant_frames(frames) == frames[:1]


def test_external_frames_are_fallback_when_no_in_app_frame_exists() -> None:
    frames = [
        StackFrame("/app/node_modules/react/index.js", "dispatch", 20, 3, False),
        StackFrame("/srv/app/server.js", "handle", 30, 4, False),
    ]
    assert select_relevant_frames(frames) == frames[1:]


def test_structured_frames_are_normalized_and_invalid_entries_skipped() -> None:
    frames = normalize_frames(
        [
            {
                "filename": "https://app.test/a.js?x=1#hash",
                "function": " async render  ",
                "lineno": 3,
                "colno": 4,
                "in_app": True,
            },
            {"filename": 42, "function": "broken"},
        ]
    )
    assert frames == [StackFrame("https://app.test/a.js", "render", 3, 4, True)]


@given(
    line1=st.integers(min_value=1, max_value=1_000_000),
    line2=st.integers(min_value=1, max_value=1_000_000),
    col1=st.integers(min_value=1, max_value=1_000_000),
    col2=st.integers(min_value=1, max_value=1_000_000),
)
def test_fingerprint_is_independent_of_line_and_column(
    line1: int, line2: int, col1: int, col2: int
) -> None:
    left = [StackFrame("/src/app.js", "render", line1, col1)]
    right = [StackFrame("/src/app.js", "render", line2, col2)]
    assert fingerprint(left) == fingerprint(right)


@given(
    query=st.text(alphabet=st.characters(blacklist_characters="#"), max_size=30),
    fragment=st.text(max_size=30),
)
def test_filename_normalization_removes_query_and_fragment(query: str, fragment: str) -> None:
    result = normalize_filename(f"https://example.test/app.js?{query}#{fragment}")
    assert result == "https://example.test/app.js"


@given(text=st.text(max_size=2_000))
def test_parser_never_raises_for_arbitrary_input(text: str) -> None:
    assert isinstance(parse_stacktrace(text), list)
