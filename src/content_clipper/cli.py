"""`uv run content-clipper <stage>`: one Typer subcommand per pipeline stage."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from pydantic import ValidationError

from content_clipper.analyze import core as an
from content_clipper.campaign import draft as campaign_draft
from content_clipper.campaign import store
from content_clipper.config import (
    CONFIG_DIR,
    load_models_config,
    load_render_config,
    load_rubric_config,
    load_transcribe_config,
)
from content_clipper.llm import LLMError
from content_clipper.render import core as rnd
from content_clipper.research import manual as research
from content_clipper.select import core as sel
from content_clipper.select import hooks as hk
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
research_app = typer.Typer(help="Research for hooks (pasted notes).", no_args_is_help=True)
app.add_typer(research_app, name="research")


@app.callback()
def _root() -> None:
    """Stages: campaign add/approve -> transcribe + analyze -> select -> render -> qa."""


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



@app.command()
def analyze(
    video: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Source video.")
    ],
    out_dir: Annotated[Path, typer.Option(help="Root for <video_id>/shots.json.")] = Path(
        "data/analysis"
    ),
) -> None:
    """Find shots in VIDEO and measure each (loudness, motion, brightness), with a keyframe."""
    import time

    t0 = time.perf_counter()
    try:
        a = an.analyze_video(video, out_dir)
    except RuntimeError as e:
        _fail(str(e))
    wall = time.perf_counter() - t0
    lengths = [s.end - s.start for s in a.shots]
    typer.echo(f"shots       {len(a.shots)} (median {sorted(lengths)[len(lengths) // 2]:.1f} s, "
               f"shortest {min(lengths):.1f} s, longest {max(lengths):.1f} s)")
    typer.echo(f"duration    {a.duration_s:.2f} s")
    typer.echo(f"wall time   {wall:.1f} s")
    typer.echo(f"wrote       {out_dir / a.video_id / 'shots.json'} + {len(a.shots)} keyframes")


def _ts(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


@app.command("select")
def select_cmd(
    campaign: str,
    video_id: Annotated[str, typer.Argument(help="Source file stem, as in data/transcripts/.")],
    max_clips: Annotated[int, typer.Option(min=1, max=20)] = 5,
    force: Annotated[bool, typer.Option(help="Overwrite an existing clips.json.")] = False,
) -> None:
    """Pick clips for CAMPAIGN from VIDEO_ID's shots, narration and keyframes."""
    model = load_models_config().selection
    typer.echo(f"Selecting clips for {campaign} from {video_id} with {model} ...")
    try:
        result = sel.select_clips(campaign, video_id, model=model, rubric=load_rubric_config(),
                                  max_clips=max_clips, force=force)
    except (store.CampaignError, sel.SelectError, LLMError, ValidationError) as e:
        _fail(str(e))
    for c in result.clips:
        flag = "  [cuts a sentence]" if c.speech_cut else ""
        typer.echo(f"\n{c.id}  {_ts(c.start_s)}-{_ts(c.end_s)}  {c.end_s - c.start_s:4.1f} s  "
                   f"score {c.score:.1f}  shots H{c.start_shot:03d}-H{c.end_shot:03d}{flag}")
        typer.echo(f"     title: {c.title}\n     why:   {c.reason}")
        _print_hooks(c)
    _bullets("Rejected candidates", [f"H{r.start_shot:03d}-H{r.end_shot:03d} {r.title}: {r.reason}"
                                     for r in result.rejected])
    typer.echo(f"\nWrote data/clips/{video_id}.clips.json. Set \"approved\": true on the clips "
               "you want rendered.")



@app.command("approve-clips")
def approve_clips(
    video_id: str,
    clip_ids: Annotated[list[str], typer.Argument(help="Clip ids, e.g. c01 c03.")],
    reject: Annotated[bool, typer.Option(help="Mark them rejected instead.")] = False,
) -> None:
    """Human sign-off: mark clips in data/clips/VIDEO_ID.clips.json approved for rendering."""
    path = Path("data/clips") / f"{video_id}.clips.json"
    if not path.exists():
        _fail(f"{path} not found")
    try:
        clips = rnd.set_approval(path, clip_ids, approved=not reject)
    except rnd.RenderError as e:
        _fail(str(e))
    for c in clips.clips:
        state = {True: "approved", False: "rejected", None: "undecided"}[c.approved]
        typer.echo(f"{c.id}  {state:10s} {c.title}")


@app.command()
def render(
    campaign: str,
    video_id: str,
    clip: Annotated[list[str] | None, typer.Option(help="Only these clip ids.")] = None,
) -> None:
    """Render CAMPAIGN's approved clips of VIDEO_ID to data/out/<campaign>/<video_id>/."""
    import time

    cfg = load_render_config()
    typer.echo(f"Rendering approved clips of {video_id} ({cfg.layout.mode} layout, "
               f"{cfg.layout.background} background, hook {cfg.layout.hook_position}) ...")
    t0 = time.perf_counter()
    try:
        done = rnd.render_approved(campaign, video_id, cfg=cfg, only=clip or None)
    except (store.CampaignError, rnd.RenderError, ValidationError) as e:
        _fail(str(e))
    for r in done:
        typer.echo(f"{r.render_json['clip_id']}  {r.duration_s:5.2f} s  {r.path}")
    typer.echo(f"wall time {time.perf_counter() - t0:.1f} s for {len(done)} clip(s)")



def _print_hooks(c) -> None:
    for i, h in enumerate(c.hook_options, start=1):
        typer.echo(f"     hook {i}{' *' if h == c.hook_line else '  '} {h}")
    if not c.hook_options:
        typer.echo(f"     hook:   {c.hook_line or '(none)'}")


def _read_input(path: Path) -> bytes:
    if str(path) == "-":
        return sys.stdin.buffer.read()
    if not path.is_file():
        _fail(f"file not found: {path}")
    return path.read_bytes()


@research_app.command("add")
def research_add(
    campaign: str,
    notes: Annotated[Path, typer.Option("--notes", help="Pasted research notes ('-' = stdin).")],
    replace: Annotated[bool, typer.Option(help="Replace earlier notes instead of adding.")] = False,
) -> None:
    """Add pasted audience research; rebuild data/research/CAMPAIGN.trend_brief.json from all notes."""
    model = load_models_config().selection
    try:
        tb = research.add_notes(campaign, _read_input(notes), model=model, replace=replace)
    except (store.CampaignError, LLMError, UnicodeDecodeError) as e:
        _fail(str(e))
    typer.echo(f"Wrote {research.trend_path(campaign)} (notes kept in {tb.sources[0]})")
    _bullets("Fan references", [f"{r.name} [{r.emotion}]: {r.context}" for r in tb.fan_references])
    _bullets("Angles hooks can lean into", [f"{x.pattern} ({x.why_it_works})" for x in tb.patterns])
    _bullets("Hook formulas", tb.hook_formulas)
    _bullets("Keep out of hooks", tb.avoid)


@app.command()
def hooks(
    campaign: str,
    video_id: str,
    clip: Annotated[list[str] | None, typer.Option(help="Only these clip ids.")] = None,
) -> None:
    """Suggest hook options for VIDEO_ID's clips (uses the trend brief). Cuts and approvals stay."""
    model = load_models_config().selection
    try:
        clips, rejected = hk.regenerate_hooks(campaign, video_id, model=model, only=clip or None)
    except (store.CampaignError, hk.HookError, LLMError, FileNotFoundError) as e:
        _fail(str(e))
    for c in clips.clips:
        if clip and c.id not in clip:
            continue
        typer.echo(f"\n{c.id}  {c.title}")
        _print_hooks(c)
        for r in rejected.get(c.id, []):
            typer.echo(f"     rejected: {r}")
    typer.echo(f"\n* = current hook. Choose with: content-clipper pick-hook {video_id!r} c01 2")


@app.command("pick-hook")
def pick_hook_cmd(video_id: str, clip_id: str, option: str) -> None:
    """Human choice: use hook option N for CLIP_ID (then re-render)."""
    try:
        c = hk.pick_hook(Path("data/clips") / f"{video_id}.clips.json", clip_id, option)
    except (hk.HookError, FileNotFoundError) as e:
        _fail(str(e))
    typer.echo(f"{c.id} hook: {c.hook_line}")


if __name__ == "__main__":
    app()
