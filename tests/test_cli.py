from typer.testing import CliRunner

from backcountry.cli import app

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("ingest", "qa", "trailheads"):
        assert command in result.output
