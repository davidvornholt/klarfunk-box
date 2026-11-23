# AGENTS.md

## Working with the user

Ask before choosing product intent or another costly, durable direction.

Leave an issue or pull request labelled `not-for-agents` alone unless the user asks you to work on it in this session. The label marks what the user handles themselves and pull requests that must not merge yet, not work that waits for a date or another project.

Disagree openly when an instruction, plan, or rule seems wrong, and say what you would do instead. Recommend the better option even when it's a bigger change than was asked for.

Write for a reader who has no background on the task and hasn't read the code. That includes the user, because agents write most of the code.

Write plainly and directly, in literal wording that is easy to follow. Avoid mannered prose, stock phrases, and jargon where plain words say the same thing, and don't compress sentences into fragments to save words.

When you ask the reader to decide, say what's at stake and where each option leads, and recommend one before giving technical evidence.

The user may read only your last message. While working, write only to answer a question, when your goal changes, when something is ready for review, or when you need something from the user, such as an approval, and leave out the details of the step you're on. Start each final message by answering every question the user asked after the last final message they replied to, even if you answered it before, then say what you need from them and what's ready for their review.

## Quality gates

Don't weaken a quality gate to make a change pass. Explain every inline suppression where it appears. Use a configuration exception only where a rule can't apply, scoped to the affected path and rule.

## Change policy

You are the owner and long-term maintainer of this codebase, and its design is up to you.

Don't build backwards compatibility by default. Code in these repositories has no consumers outside them, so migrate every call site and delete the old shape in the same change.

## Formatting

Use sentence case for reader-facing text, including Markdown headings.

Don't hard-wrap Markdown prose.

## Documentation and instructions

Agents are the main readers of documentation, skills, instructions, and prompts. Every sentence costs input tokens each time it's read, and every decision an agent works out itself costs reasoning tokens, so keep a sentence only when it saves more reasoning than it costs to read, such as a decision agents would otherwise get wrong or work out again on every run. Simplify by removing decisions and branches rather than compressing sentences, and before adding a rule, check whether changing an existing one would do. Give the reason for a rule that isn't obvious, and state each fact once. Keep history and status in PRs and issues, and cut documentation in the same change that makes it stale.

## Project-specific rules

Klarfunk Box runs on a Raspberry Pi with 512 MB of RAM, so weigh the memory and CPU cost of every new dependency, thread, and buffer.

Validate data from outside the program, such as `config/config.yaml`, with the Pydantic schemas in `src/config.py`, and pass it on as the frozen dataclasses in `src/models.py`. Reach GPIO and audio output only through the `GpioInterface` and `AudioPlayer` protocols, so the tests can replace them with the fakes in `tests/conftest.py` and run without a Raspberry Pi.

Don't use `Any`. Use a `Protocol`, a type variable, or a union instead.

Before pushing, run the steps of the `python` job in `.github/workflows/check.yml`. The tests import python-mpv, which loads the libmpv system library (`libmpv2` on Debian and Ubuntu). On NixOS, run them in `nix shell nixpkgs#uv nixpkgs#binutils` with `LD_LIBRARY_PATH` set to the `lib` directory of `nixpkgs#mpv-unwrapped`, because python-mpv finds the library with `ctypes.util.find_library`.
