"""Constrained extraction prompt. Kept deterministic (no timestamps) for caching."""

from __future__ import annotations

from ..ingest.pdf_reader import ExtractedPdf
from .base import InferencePrompt

SYSTEM_PROMPT = """You convert a written process description into a candidate process graph for human review.

Report only what the document states. The output is a draft that a person will check against the source, so an omission is far better than an invention.

Rules:
1. Steps: include a task, event or decision only if the text describes it. Use kind "decision" for an explicit question or branch point. Include start_event/end_event only when the text describes how the process starts or ends.
2. Evidence: every step, flow, input, output and uncertainty needs at least one evidence entry with the 1-based page number and a quote copied character-for-character from that page (8 to 200 characters). Do not paraphrase inside quotes. Quotes are checked automatically against the PDF text; any quote that is not found is flagged as fabricated.
3. Actors: set actor to the role the text names for that step. If the text names no actor, use exactly "not_stated". Do not guess roles from context.
4. Inputs and outputs: list only items the text names as consumed or produced by that step. Use an empty list when none are stated. A control-flow transition is not an input or output.
5. Flows: connect steps in the order the text gives. For a branch from a decision, put the stated condition (for example "Yes" or "No") in condition. Use "none" for an unconditional flow and "not_stated" when a branch exists but its condition is not written.
6. Ambiguity: when the text mentions a possible step, loop, actor or condition without saying who, when or under which condition, do not add that step or flow. Record it in uncertainties with its evidence and the ids of related steps instead.
7. Ignore headings, version notes, footnotes and instructions to the reader unless they describe process behaviour.
8. Ids: short and unique, such as S1, S2 for steps and F1, F2 for flows. Flow source and target must be step ids from your list.
9. process_title: the title stated in the document, or "not_stated"."""


def build_prompt(pdf: ExtractedPdf, filename: str) -> InferencePrompt:
    parts = [f"Document file name: {filename}", f"Pages: {pdf.page_count}", "",
             "Extracted text blocks in reading order. Each line starts with [page N | block id].", ""]
    for b in pdf.blocks:
        text = b.text.replace("\n", " ")
        role = " | heading" if b.role == "heading" else ""
        parts.append(f"[page {b.page} | {b.id}{role}] {text}")
    if pdf.tables:
        parts += ["", "Tables detected (rows separated by newlines, cells by ' | '):"]
        for t in pdf.tables:
            parts.append(f"[page {t['page']} | {t['id']}]")
            parts += [" | ".join(r) for r in t["rows"]]
    if pdf.pages_without_text:
        parts += ["", f"Pages without extractable text (not analysed): {pdf.pages_without_text}"]
    parts += ["", "Return the candidate process as JSON matching the schema."]
    return InferencePrompt(system=SYSTEM_PROMPT, user="\n".join(parts))
