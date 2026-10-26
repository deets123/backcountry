import hashlib

import pytest
from typer.testing import CliRunner

from backcountry import usdata
from backcountry.cli import app
from backcountry.ingest import FetchError

BODY = b"not really a pbf, just bytes"
GOOD_MD5 = hashlib.md5(BODY).hexdigest()


def fakes(md5_text, last_modified="Sat, 24 Oct 2026 20:41:00 GMT"):
    seen = []

    def fetch_bytes(url, part):
        seen.append(url)
        part.write_bytes(BODY)
        return {"Last-Modified": last_modified} if last_modified else {}

    def fetch_text(url):
        seen.append(url)
        return md5_text

    return seen, fetch_bytes, fetch_text


def test_md5_match_names_by_last_modified(tmp_path):
    _, fb, ft = fakes(f"{GOOD_MD5}  us-latest.osm.pbf\n")
    path = usdata.download_extract(tmp_path, fetch_bytes=fb, fetch_text=ft)
    assert path.name == "us-2026-10-24.osm.pbf" and path.read_bytes() == BODY
    assert list(tmp_path.iterdir()) == [path]  # no .part left


def test_md5_mismatch_discards_file(tmp_path):
    _, fb, ft = fakes("0" * 32 + "  us-latest.osm.pbf\n")
    with pytest.raises(FetchError, match="checksum"):
        usdata.download_extract(tmp_path, fetch_bytes=fb, fetch_text=ft)
    assert list(tmp_path.iterdir()) == []


def test_malformed_md5_file_refused(tmp_path):
    _, fb, ft = fakes("<html>not found</html>")
    with pytest.raises(FetchError):
        usdata.download_extract(tmp_path, fetch_bytes=fb, fetch_text=ft)
    assert list(tmp_path.iterdir()) == []


def test_no_last_modified_uses_today(tmp_path, monkeypatch):
    monkeypatch.setattr(usdata, "_today", lambda: "2026-10-25")
    _, fb, ft = fakes(f"{GOOD_MD5}  us-latest.osm.pbf", last_modified=None)
    assert usdata.download_extract(tmp_path, fetch_bytes=fb, fetch_text=ft).name == "us-2026-10-25.osm.pbf"


def test_download_only_fixed_host(tmp_path):
    seen, fb, ft = fakes(f"{GOOD_MD5}  us-latest.osm.pbf")
    usdata.download_extract(tmp_path, fetch_bytes=fb, fetch_text=ft)
    assert seen and all(u.startswith("https://download.geofabrik.de/") for u in seen)


def test_downloaded_file_not_executable(tmp_path):
    _, fb, ft = fakes(f"{GOOD_MD5}  us-latest.osm.pbf")
    path = usdata.download_extract(tmp_path, fetch_bytes=fb, fetch_text=ft)
    assert path.stat().st_mode & 0o111 == 0


def test_current_index_picks_newest(tmp_path):
    assert usdata.current_index(tmp_path) is None
    for d in ("2026-09-01", "2026-10-20", "2026-10-02"):
        (tmp_path / f"us-{d}.sqlite").write_text("")
    (tmp_path / "us-2026-11-01.sqlite.tmp").write_text("")  # half-built: ignored
    assert usdata.current_index(tmp_path).name == "us-2026-10-20.sqlite"


def test_build_command_names_index_by_extract_date(tmp_path, monkeypatch):
    from conftest import FIXTURES

    monkeypatch.setattr(usdata, "OSM_DIR", tmp_path)
    src = tmp_path / "us-2026-10-20.osm"  # the fixture is OSM XML; osmium reads it like a .pbf
    src.write_bytes((FIXTURES / "mini.osm").read_bytes())
    result = CliRunner().invoke(app, ["usdata", "build", "--pbf", str(src)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "us-2026-10-20.sqlite").exists()
    status = CliRunner().invoke(app, ["usdata", "status"])
    assert "2026-10-20" in status.output


def test_build_without_extract_says_to_download(tmp_path, monkeypatch):
    monkeypatch.setattr(usdata, "OSM_DIR", tmp_path)
    result = CliRunner().invoke(app, ["usdata", "build"])
    assert result.exit_code == 1 and "usdata download" in result.output


@pytest.mark.parametrize("cmd", ["download", "build", "refresh", "status"])
def test_cli_has_no_url_option(cmd):
    out = CliRunner().invoke(app, ["usdata", cmd, "--help"]).output
    assert "--url" not in out and "http" not in out.lower()
