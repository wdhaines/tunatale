"""CLI-friendly text-report helpers for Norwegian Pimsleur breakdown preview."""

from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown,
    segment_compound,
    slow_norwegian_word,
)


def format_breakdown_preview(phrase: str) -> str:
    """Return a multi-line text report showing the breakdown of *phrase*."""
    phrase = phrase.strip()
    if not phrase:
        return ""

    lines: list[str] = []
    lines.append(f'=== Breakdown Preview: "{phrase}" ===')

    # Compound segments
    parts: list[str] = []
    for word in phrase.split():
        parts.extend(segment_compound(word))
    lines.append(f"  Compound segments:  {' | '.join(parts)}")

    # Slow pronunciation
    words_slowed = []
    for word in phrase.split():
        w = slow_norwegian_word(word)
        words_slowed.append(w)
    slow_display = " ... ".join(words_slowed)
    lines.append(f"  Slow pronunciation:  {slow_display}")

    # Pimsleur breakdown steps
    steps = build_norwegian_breakdown(phrase)
    steps_display = " \u2192 ".join(steps)
    lines.append(f"  Pimsleur steps:      {steps_display}")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover — CLI guard
    # Delegate to the audio CLI (prints this text report per word, then renders).
    from app.plugins.languages.no.breakdown_audio import main

    main()
