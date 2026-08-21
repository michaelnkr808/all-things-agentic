"""Adversarial veto checker (Michael).

Gets the original prompt, the environment config, the CompiledOutput, and the
raw gathered material. Its job is to try to reject: unsupported claims,
missing [[source]] links, departments used that the config doesn't allow,
prompt not actually answered. Returns a VetoVerdict; on veto, revision_notes
must be concrete enough for state_manager.revise() to act on.

Runs on the strong model (config.models.veto) with an adversarial system
prompt — it should APPROVE only when it fails to find a real problem.
"""

from __future__ import annotations

from agentic.contracts.config import EnvironmentConfig
from agentic.contracts.messages import CompiledOutput, GatherResult, VetoVerdict

import anthropic

client = anthropic.AsyncAnthropic()

# Per-file cap on source text sent to the checker. Gatherers over-gather by
# design, so without a cap one verbose file can dominate the request.
MAX_SOURCE_CHARS = 4000

SYSTEM_PROMPT = """\
You are the veto checker: the last review an answer passes through before a \
user sees it. Everything upstream of you — the planner, the gatherers, the \
synthesizer — is optimizing to produce an answer. You are the only component \
optimizing to catch one that shouldn't ship. Read the answer looking for the \
reason it is wrong, not for confirmation that it is right.

You are given four things: the user's original prompt, a summary of the \
environment configuration (which departments exist and which roles may read \
them), the compiled answer in obsidian-flavored markdown, and the source \
material the answer was built from. Sources are cited as [[department/path]] \
wikilinks, and each department is tagged #name.

The source material section contains the full text of every file the answer \
cites, so you can check claims against it directly rather than taking the \
citation's word for it. Long files are truncated; a truncation marker means \
absence of evidence is not evidence of absence for that file, so do not veto \
a claim solely because it falls past the cut. That section also lists files \
the gatherers retrieved but the answer never cited — the gatherers \
deliberately over-gather, so an uncited file is not by itself a problem, but \
an uncited file that plainly answers a part of the question the answer left \
unanswered is.

Veto the answer if any of the following is true:

1. A factual claim in the answer does not appear in the source material. \
Numbers, dates, names, and totals are the usual offenders — check each one \
against the cited files. General framing and connective prose do not need \
citations; specific assertions do.
2. The answer cites a source from a department the configuration does not \
list, or the sources contradict the departments recorded in the frontmatter.
3. The answer does not address what the user actually asked. An answer that \
is true, well-sourced, and about a different question is still a failure.
4. The answer has no sources at all, or claims to have found information \
while listing none.
5. The answer states something the cited material does not support, or \
overstates a hedged source as a certainty. A source saying "roughly $40k" \
does not support an answer saying "$42,300".

Do not veto for style, length, tone, formatting, or the absence of caveats \
you would have added. Do not veto because the answer is thinner than you \
would like when the gathered material genuinely did not contain more. A \
narrow answer drawn honestly from narrow evidence is a pass. Vetoing \
everything is the same failure as approving everything: it tells the state \
manager nothing about what to fix, and the retry budget is small.

Approve only when you have looked for the problems above and failed to find \
one. If you find one, veto.

Fill the verdict as follows. When you veto, `reasons` states each problem you \
found in one sentence, quoting the specific claim or citation at fault, and \
`revision_notes` gives instructions the state manager can act on without \
seeing your reasoning — name the claim to remove, the citation to add, or the \
part of the question still unanswered. "Improve the sourcing" is not \
actionable; "the $42k infra figure in paragraph two is uncited — cite it or \
drop it" is. When you approve, leave `revision_notes` empty and use `reasons` \
to record briefly what you checked and why it held up.\
"""


def config_summary(config: EnvironmentConfig) -> str:
    """One line per department: name + who may read it."""
    lines = []
    for d in config.departments:
        lines.append(f"{d.name}: {', '.join(d.allowed_roles)}")

    return "\n".join(lines)


def source_material(compiled: CompiledOutput, results: list[GatherResult]) -> str:
    """Full text of every cited file, plus a bare list of the uncited ones.

    Cited files are what claims get checked against. Uncited files are named
    but not dumped: enough for the checker to notice the answer ignored
    something relevant, without paying for the gatherers' over-gather bias.
    """
    cited = set(compiled.sources)

    # The same file can arrive more than once: two planner tasks aimed at one
    # department, or overlapping file_globs within a single gatherer. Emitting
    # its text twice wastes tokens and reads as corroboration that isn't there.
    seen: set[str] = set()

    blocks: list[str] = []
    uncited: list[str] = []
    for result in results:
        for f in result.files:
            key = f"{f.department}/{f.path}"
            if key in seen:
                continue
            seen.add(key)
            if key not in cited:
                uncited.append(key)
                continue
            content = f.content
            if len(content) > MAX_SOURCE_CHARS:
                content = content[:MAX_SOURCE_CHARS] + "\n[... truncated ...]"
            blocks.append(f"--- [[{key}]] ---\n{content}")

    if not blocks:
        blocks.append("(no cited files were present in the gathered material)")

    text = "\n\n".join(blocks)
    if uncited:
        text += "\n\nGathered but not cited by the answer:\n" + "\n".join(
            f"- {u}" for u in uncited
        )
    return text


async def check(
    prompt: str,
    compiled: CompiledOutput,
    config: EnvironmentConfig,
    results: list[GatherResult],
) -> VetoVerdict:
    user_message = (
        f"ORIGINAL PROMPT:\n{prompt}\n\n"
        f"ENVIRONMENT CONFIG:\n{config_summary(config)}\n\n"
        f"COMPILED ANSWER:\n{compiled.obsidian_markdown}\n\n"
        f"SOURCE MATERIAL:\n{source_material(compiled, results)}"
    )
    response = await client.beta.messages.parse(
        model=config.models.veto,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=VetoVerdict,
        # Safety classifiers can decline a request outright (HTTP 200,
        # stop_reason "refusal"). Rather than burning a retry on the guard
        # below, let the API re-run the same request on another model inside
        # this call. "default" routes by refusal category, so there is no
        # model list to keep current.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )

    # Fail closed. `parsed_output` is Optional: a safety refusal, a max_tokens
    # truncation mid-JSON, or an oversized context all return HTTP 200 with no
    # parsed verdict. Returning None here would surface as an AttributeError in
    # the pipeline's retry loop; an unreviewed answer must never read as
    # approved, so we veto and say why.
    verdict = response.parsed_output
    if response.stop_reason != "end_turn" or verdict is None:
        detail = f"stop_reason={response.stop_reason!r}"
        if response.stop_details is not None:
            detail += f" ({response.stop_details.type})"
        return VetoVerdict(
            approved=False,
            reasons=[f"The veto check did not complete: {detail}."],
            revision_notes=(
                "The reviewer returned no verdict, so this answer is unreviewed "
                "rather than known-bad. Resubmit it unchanged for another check."
            ),
        )

    return verdict
