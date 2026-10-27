"""Plans that run in the background, so the phone can show the first trips while the search goes on.

POST /regions/{id}/plans starts one and returns its id; GET /plans/{id} returns the trips found so far
("searching", not saved) or the final run ("done", saved like /generate saves it). At most MAX_PLAN_JOBS run at a
time for everyone (no accounts yet); finished jobs are forgotten JOB_TTL_S after they finish.
"""

import logging
import re
import secrets
import threading
import time

from backcountry.api.area_worker import Busy
from backcountry.api.store import NotFound
from backcountry.planning import RequestError

MAX_PLAN_JOBS = 2
JOB_TTL_S = 600.0
FAILED = "Couldn't plan this trip. Try again."
_JOB_ID = re.compile(r"^[0-9a-f]{16}$")
log = logging.getLogger("backcountry.api")


class PlanJobs:
    def __init__(self, settings, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _expire(self) -> None:
        now = self.clock()
        for job_id in [j for j, job in self._jobs.items() if job["finished"] is not None and now - job["finished"] > JOB_TTL_S]:
            del self._jobs[job_id]

    def start(self, work) -> str:
        """Run `work(on_snapshot) -> run` and keep what it hands over. Raises Busy when too many are running."""
        with self._lock:
            self._expire()
            if sum(1 for j in self._jobs.values() if j["status"] == "searching") >= MAX_PLAN_JOBS:
                raise Busy("The server is busy planning other trips. Try again in a minute.")
            job_id = secrets.token_hex(8)
            self._jobs[job_id] = {"status": "searching", "run": None, "message": None, "field": None, "finished": None}
        if self.settings.area_thread:
            threading.Thread(target=self._run, args=(job_id, work), daemon=True).start()
        else:
            self._run(job_id, work)  # tests: synchronous
        return job_id

    def _set(self, job_id: str, **fields) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def _run(self, job_id: str, work) -> None:
        try:
            run = work(lambda partial: self._set(job_id, run=partial))
            self._set(job_id, status="done", run=run, finished=self.clock())
        except RequestError as e:
            self._set(job_id, status="failed", message=str(e), field=e.field, finished=self.clock())
        except NotFound as e:
            self._set(job_id, status="failed", message=str(e), finished=self.clock())
        except Exception:
            log.exception("plan job %s failed", job_id)  # details stay in the server log
            self._set(job_id, status="failed", message=FAILED, finished=self.clock())

    def get(self, job_id: str) -> dict:
        if not isinstance(job_id, str) or not _JOB_ID.match(job_id):
            raise NotFound("No such plan.")
        with self._lock:
            self._expire()
            job = self._jobs.get(job_id)
            if job is None:
                raise NotFound("No such plan (it may have expired).")
            return {k: job[k] for k in ("status", "run", "message", "field")}
