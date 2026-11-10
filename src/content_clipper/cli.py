"""`uv run content-clipper <stage>`: one Typer subcommand per pipeline stage."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from pydantic import ValidationError

from content_clipper.campaign import draft as campaign_draft
from content_clipper.campaign import store
from content_clipper.config import (
    CONFIG_DIR,
    load_models_config,
    load_transcribe_config,
)
from content_clipper.llm import LLMError
from content_clipper.transcribe import core as tx

app = typer.Typer(
    help="Turn campaign-authorized long videos into captioned 9:16 short clips.",
    no_args_is_help=True,
    add_completion=False,
)
campaign_app = typer.Typer(
    help="Campaign briefs and rules (campaigns/<name>/).", no_args_is_help=True
)
app.add_typer(campaign_app, name="campaign")


@app.callback()
def _root() -> None:
    """Stages: campaign add/approve -> transcribe -> select -> approve -> render -> qa."""


def _fail(msg: str) -> NoReturn:
    typer.secho(f"error: {msg}", err=True, fg="red")
    raise typer.Exit(1)


def _bullets(title: str, items: list[str]) -> None:
    if items:
        typer.echo(f"\n{title}:")
        for item in items:
            typer.echo(f"  - {item}")


def _load(name: str) -> store.Campaign:
    try:
        return store.load_campaign(name)
    except (store.CampaignError, ValidationError) as e:
        _fail(str(e))


@campaign_app.command("add")
def campaign_add(
    name: Annotated[str, typer.Argument(help="Campaign name: lowercase letters, digits, dashes.")],
    brief: Annotated[
        Path, typer.Option("--brief", help="File holding the brief pasted verbatim ('-' = stdin).")
    ],
    force: Annotated[bool, typer.Option(help="Redraft an existing campaign (discards edits).")] = False,
) -> None:
    """Store BRIEF as campaigns/NAME/brief.md and draft rules.yaml with the LLM (status: draft)."""
    if str(brief) == "-":
        brief_bytes = sys.stdin.buffer.read()
    elif brief.is_file():
        brief_bytes = brief.read_bytes()
    else:
        _fail(f"brief file not found: {brief}")
    model = load_models_config().selection
    typer.echo(f"Drafting rules for {name} with {model} ...")
    try:
        rules, v = campaign_draft.add_campaign(name, brief_bytes, model=model, force=force)
    except (store.CampaignError, LLMError) as e:
        _fail(str(e))

    d = store.campaign_dir(name)
    typer.echo(f"Wrote {d / 'brief.md'} (verbatim) and {d / 'rules.yaml'} (status: draft)")
    typer.echo(f"Values backed by a verified quote: {v.verified}. Rejected: {len(v.rejections)}.")
    _bullets("Rejected (reset to default, question added)",
             [f"{r.path}: {r.reason}" for r in v.rejections])
    _bullets("Safety defaults", store.safety_notes(rules))
    _bullets("Problems that block approval", store.problems(rules))
    _bullets("Other rules (no schema field; checked by LLM review and by you)", rules.other_rules)
    _bullets("Could not verify (fix in rules.yaml; approve refuses until this is empty)",
             rules.unknowns)
    typer.echo(
        f"\nNext: read {d / 'rules.yaml'} next to brief.md, fix anything wrong, then run\n"
        f"  uv run content-clipper campaign approve {name}"
    )


@campaign_app.command("show")
def campaign_show(name: str) -> None:
    """Show a campaign's effective status, warnings and rules."""
    c = _load(name)
    note = " (rules.yaml says reviewed)" if c.rules.status != c.status else ""
    typer.echo(f"campaign  {c.name}\nstatus    {c.status}{note}")
    for w in c.warnings:
        typer.secho(f"WARNING   {w}", fg="yellow")
    for n in store.safety_notes(c.rules):
        typer.echo(f"note      {n}")
    for p in store.problems(c.rules):
        typer.secho(f"PROBLEM   {p} (blocks approval)", fg="red")
    body = c.rules.body().model_dump(mode="json", exclude={"unknowns"})
    typer.echo("\n" + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100))
    _bullets("Unverified values (fix before approving)", c.rules.unknowns)
    typer.echo(f"\nEvidence quotes: {len(c.rules.evidence)} (see rules.yaml)")


@campaign_app.command("approve")
def campaign_approve(name: str) -> None:
    """Human sign-off: mark rules reviewed and pin the brief.md hash."""
    try:
        c = store.approve(name)
    except (store.CampaignError, ValidationError) as e:
        _fail(str(e))
    typer.secho(f"Approved {name}: status reviewed, brief_sha256 pinned.", fg="green")
    for w in c.warnings:
        typer.secho(f"WARNING   {w}", fg="yellow")
    _bullets("Safety defaults", store.safety_notes(c.rules))


@app.command()
def checklist(campaign: str) -> None:
    """Print what a human must check for CAMPAIGN: things code cannot control."""
    c = _load(campaign)
    typer.echo(f"Checklist for {c.name} (status: {c.status})")
    for w in c.warnings:
        typer.secho(f"WARNING   {w}", fg="yellow")
    for title, items in store.checklist_sections(c.rules).items():
        if items:
            typer.echo(f"\n{title}:")
            for item in items:
                typer.echo(f"  [ ] {item}")


@app.command()
def transcribe(
    video: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, readable=True, help="Source video or audio."),
    ],
    out_dir: Annotated[
        Path, typer.Option(help="Where <video_id>.json is written.")
    ] = Path("data/transcripts"),
    config: Annotated[
        Path, typer.Option(exists=True, dir_okay=False, help="Transcribe settings.")
    ] = CONFIG_DIR / "transcribe.yaml",
) -> None:
    """Transcribe VIDEO with word timestamps and write the transcript contract."""
    cfg = load_transcribe_config(config)
    typer.echo(
        f"Transcribing {video} (model={cfg.model_size}, {cfg.device}/{cfg.compute_type}, "
        f"beam={cfg.beam_size}, vad={cfg.vad_filter})"
    )

    last_bucket = -1

    def progress(end_s: float, duration_s: float) -> None:
        nonlocal last_bucket
        if duration_s <= 0:
            return
        bucket = min(int(10 * end_s / duration_s), 10)
        if bucket > last_bucket:
            last_bucket = bucket
            typer.echo(f"  ... {end_s:7.1f} / {duration_s:.1f} s", err=True)

    res = tx.transcribe_file(video, cfg, on_progress=progress)
    path = tx.write_transcript(res.transcript, out_dir)
    t = res.transcript

    typer.echo(f"duration    {t.duration_s:.2f} s")
    typer.echo(f"model load  {res.load_s:.2f} s")
    typer.echo(f"wall time   {res.wall_s:.2f} s")
    typer.echo(f"RTF         {res.rtf:.3f}  (wall time / duration; below 1 = faster than real time)")
    typer.echo(
        f"segments    {len(t.segments)}, words {len(t.words)}, "
        f"word timings clamped {res.adjusted_words}, language {t.language}"
    )
    typer.echo(f"wrote       {path}")


if __name__ == "__main__":
    app()
