import threading

import pytest

from backcountry.api.area_worker import Busy
from backcountry.api.plan_jobs import JOB_TTL_S, MAX_PLAN_JOBS, PlanJobs
from backcountry.api.store import NotFound, Settings
from backcountry.planning import RequestError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def wait_done(jobs, job_id):
    for _ in range(200):
        if jobs.get(job_id)["status"] != "searching":
            return jobs.get(job_id)
        threading.Event().wait(0.01)
    raise AssertionError("job never finished")


def test_plan_jobs_are_capped_and_expire():
    clock, release = Clock(), threading.Event()

    def blocking(on_snapshot):
        release.wait(5)
        return {"run_id": "r1"}

    jobs = PlanJobs(Settings(area_thread=True), clock=clock)
    running = [jobs.start(blocking) for _ in range(MAX_PLAN_JOBS)]
    with pytest.raises(Busy):
        jobs.start(blocking)
    release.set()
    for j in running:
        assert wait_done(jobs, j)["status"] == "done"
    clock.t += JOB_TTL_S + 1
    with pytest.raises(NotFound):
        jobs.get(running[0])


def test_job_shows_the_snapshot_then_the_final_run():
    seen = []
    jobs = PlanJobs(Settings(area_thread=False))

    def work(on_snapshot):
        on_snapshot({"run_id": "partial", "partial": True})
        seen.append(jobs.get(next(iter(jobs._jobs)))["run"])
        return {"run_id": "r1", "partial": False}

    job = jobs.get(jobs.start(work))
    assert seen == [{"run_id": "partial", "partial": True}]
    assert job["status"] == "done" and job["run"]["run_id"] == "r1"


def test_job_failures_say_what_went_wrong_in_plain_words():
    jobs = PlanJobs(Settings(area_thread=False))

    def bad_setting(on_snapshot):
        raise RequestError("trailhead", "No trailhead called X here")

    def crash(on_snapshot):
        raise RuntimeError("stack trace details")

    a = jobs.get(jobs.start(bad_setting))
    assert a["status"] == "failed" and a["message"] == "No trailhead called X here" and a["field"] == "trailhead"
    b = jobs.get(jobs.start(crash))
    assert b["status"] == "failed" and "stack trace" not in b["message"]


@pytest.mark.parametrize("bad", ["../etc", "x" * 16, "ABCDEF0123456789", ""])
def test_job_ids_are_checked(bad):
    with pytest.raises(NotFound):
        PlanJobs(Settings(area_thread=False)).get(bad)


def test_the_cap_is_a_setting():
    release = threading.Event()
    jobs = PlanJobs(Settings(area_thread=True, plan_jobs=3))
    for _ in range(3):
        jobs.start(lambda on_snapshot: release.wait(5) or {"run_id": "r"})
    with pytest.raises(Busy):
        jobs.start(lambda on_snapshot: {"run_id": "r"})
    release.set()


def test_a_cancelled_job_frees_its_slot():
    release = threading.Event()
    jobs = PlanJobs(Settings(area_thread=True, plan_jobs=2))
    a = jobs.start(lambda on_snapshot: release.wait(5) or {"run_id": "a"})
    jobs.start(lambda on_snapshot: release.wait(5) or {"run_id": "b"})
    jobs.cancel(a)  # the phone gave up on it (a new plan, or its time limit)
    c = jobs.start(lambda on_snapshot: {"run_id": "c"})
    assert jobs.get(a)["status"] == "cancelled"
    release.set()
    assert wait_done(jobs, c)["status"] == "done"
    for _ in range(200):
        if jobs.get(a)["run"] is None and jobs.get(a)["status"] == "cancelled":
            break
    assert jobs.get(a)["status"] == "cancelled" and jobs.get(a)["run"] is None  # a late result is dropped

