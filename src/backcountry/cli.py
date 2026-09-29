"""Command-line interface: `backcountry <command> --region <id>`."""

import typer

app = typer.Typer(help="Backcountry route engine (Phase 1: Dolly Sods, CLI only).", no_args_is_help=True)


@app.command()
def ingest(
    region: str = typer.Option(..., help="Region id, e.g. dolly_sods"),
    refresh: bool = typer.Option(False, help="Re-query Overpass even if a cached response exists."),
):
    """Fetch OSM trail data for a region and cache the raw response."""
    raise typer.Exit(code=_not_yet("ingest"))


@app.command()
def qa(region: str = typer.Option(..., help="Region id, e.g. dolly_sods")):
    """Build the trail graph and write a QA report + component map."""
    raise typer.Exit(code=_not_yet("qa"))


@app.command()
def trailheads(region: str = typer.Option(..., help="Region id, e.g. dolly_sods")):
    """List candidate trailheads found in source data."""
    raise typer.Exit(code=_not_yet("trailheads"))


def _not_yet(name: str) -> int:
    typer.echo(f"`{name}` is not implemented yet (M1).", err=True)
    return 1
