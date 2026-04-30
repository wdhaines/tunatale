# TDD Workflow

## Red-Green-Refactor

1. **Red**: Write a failing test that describes the desired behavior
2. **Green**: Write the minimum code to make the test pass
3. **Refactor**: Clean up without breaking the test

## Rules

- Never write implementation before the test file exists
- Each plan step: write ALL tests for that step first, then implement
- Tests must fail before implementation (verify with `pytest -x`)
- After each step: `./test.sh` must pass (lint + all tests + coverage)
- **Never declare victory with `./test.sh` failing** — fix all errors before moving on
- **Never commit with failing tests or coverage failures**

## Plan Step Ordering

Steps are ordered by dependency. Never implement step N+1 until step N's tests are green.

Current steps:
1. Project scaffolding & health check ← here
2. Domain models
3. Groq LLM client + cassette system
4. FSRS SRS engine
5. Curriculum generation
6. Story generation + enforcement
7. Audio pipeline — ports & preprocessing
8. Audio pipeline — EdgeTTS + assembly
9. API layer — full pipeline
10. Simple web UI
