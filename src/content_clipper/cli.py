"""`uv run content-clipper <stage>`: one Typer subcommand per pipeline stage."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from content_clipper.config import CONFIG_DIR, load_transcribe_config
from content_clipper.transcribe import core as tx

app = typer.Typer(
    help="Turn campaign-authorized long videos into captioned 9:16 short clips.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """Stages: ingest -> transcribe -> select -> approve -> render -> qa."""


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
