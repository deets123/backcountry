"""Run the API on the synthetic grid network, for the web app's end-to-end test.

    uv run python scripts/test_api_server.py   # listens on $BACKCOUNTRY_TEST_PORT or 8765
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import build_grid  # noqa: E402

from backcountry.api.app import create_app  # noqa: E402
from backcountry.api.store import Settings  # noqa: E402
from backcountry.chat.models import FakeModel  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="backcountry-e2e-"))
(tmp / "raw" / "dolly_sods").mkdir(parents=True)
(tmp / "raw" / "dolly_sods" / "overpass_20260101T000000Z.json").write_text(json.dumps(build_grid().doc()))
# The end-to-end test never needs Ollama: the chat always proposes lowering the daily maximum.
CHAT_ANSWER = {"reply": "This lowers the most miles allowed in a day.", "kind": "change", "changes": {"daily": [0.5, 3]}}
# Playwright runs several browsers at once against this one server, so it allows more plans at a time.
app = create_app(Settings(raw_dir=tmp / "raw", runs_dir=tmp / "runs", regions_dir=None, plan_jobs=8), web_dist=tmp / "no-web",
                 chat_model=FakeModel([CHAT_ANSWER] * 100))
uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BACKCOUNTRY_TEST_PORT", "8765")), log_level="warning")
