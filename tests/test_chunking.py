"""Chapter-heading detection must not mistake a wrapped exercise question or a numeric example for a heading."""
from app.ingestion.chunking import chunk_text, summarise_chapters


def test_numbered_exercise_question_is_not_mistaken_for_a_heading():
    text = ("# Chapter 1: Patterns in Mathematics\n\n"
            + "Mathematics is the search for patterns. " * 12 + "\n\n"
            "9. Find your own patterns or relations in and among the numbers given below.\n"
            "2 4 6 8 10\n\n"
            + "Look at sequences of numbers and find the rule that connects them. " * 12)
    chunks = chunk_text(text)
    chapters = {c.chapter for c in chunks}
    assert chapters == {"Chapter 1: Patterns in Mathematics"}
    assert "Find your own patterns" not in chapters
    assert "2 4 6 8 10" not in chapters


def test_short_numbered_and_all_caps_headings_are_still_detected():
    text = ("Some intro text to close off the previous section nicely here. " * 10 + "\n\n"
            "9. Quadratic Equations\n\n" + "A quadratic equation has the form ax^2+bx+c=0. " * 12 + "\n\n"
            # a genuine running header repeats on every page; a one-off ALL-CAPS line further below must not count
            "PATTERNS IN NUMBERS\n\n" + "A number pattern is a list that follows a rule. " * 12 + "\n\n"
            "PATTERNS IN NUMBERS\n\n" + "Here is another section about the same pattern family. " * 12)
    chunks = chunk_text(text)
    chapters = [c.chapter for c in chunks]
    assert "9. Quadratic Equations" in chapters
    assert "Patterns In Numbers" in chapters


def test_a_one_off_all_caps_fragment_is_not_mistaken_for_a_running_header():
    text = ("PATTERNS IN\n\nMathematics is the search for patterns, " * 1 + "and much more besides. " * 12 + "\n\n"
            + "A number pattern is a list that follows a rule and repeats. " * 12)
    chunks = chunk_text(text)
    assert "Patterns In" not in [c.chapter for c in chunks]


def test_explicit_chapter_marker_survives_even_if_phrased_as_a_question():
    text = "Chapter 1: What is Democracy?\n\n" + "Democracy is a form of government. " * 12
    chunks = chunk_text(text)
    assert chunks[0].chapter == "Chapter 1: What is Democracy?"


def test_a_bare_numeric_example_line_is_not_mistaken_for_a_chapter_number():
    text = ("# Patterns in Mathematics\n\n" + "A sequence is a list of numbers following a rule. " * 12 + "\n\n"
            "2  4  6  8  10  numbers\n\n" + "Notice how each term increases by the same amount each time. " * 12)
    chunks = chunk_text(text)
    assert "2  4  6  8  10  numbers" not in [c.chapter for c in chunks]


def test_solutions_appendix_is_a_real_heading_but_excluded_from_topics():
    text = ("Chapter 1: Patterns in Mathematics\n\n" + "A sequence follows a clear and consistent rule. " * 12 + "\n\n"
            "Chapter 1 -- Solutions\n\n" + "Answer to question one is given below in full detail. " * 12)
    chunks = chunk_text(text)
    assert any("Solutions" in c.chapter for c in chunks)  # still a real, detected heading
    titles = {c["title"] for c in summarise_chapters(chunks)}
    assert "Chapter 1: Patterns in Mathematics" in titles
    assert not any("Solutions" in t for t in titles)  # but not offered as an exam topic
