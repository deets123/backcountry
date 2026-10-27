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

    def blocking(on_snapshot, stop):
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

    def work(on_snapshot, stop):
        on_snapshot({"run_id": "partial", "partial": True})
        seen.append(jobs.get(next(iter(jobs._jobs)))["run"])
        return {"run_id": "r1", "partial": False}

    job = jobs.get(jobs.start(work))
    assert seen == [{"run_id": "partial", "partial": True}]
    assert job["status"] == "done" and job["run"]["run_id"] == "r1"


def test_job_failures_say_what_went_wrong_in_plain_words():
    jobs = PlanJobs(Settings(area_thread=False))

    def bad_setting(on_snapshot, stop):
        raise RequestError("trailhead", "No trailhead called X here")

    def crash(on_snapshot, stop):
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
        jobs.start(lambda on_snapshot, stop: release.wait(5) or {"run_id": "r"})
    with pytest.raises(Busy):
        jobs.start(lambda on_snapshot, stop: {"run_id": "r"})
    release.set()


def test_a_cancelled_job_stops_its_work_and_frees_its_slot():
    jobs = PlanJobs(Settings(area_thread=True, plan_jobs=2))
    stopped = threading.Event()

    def obedient(on_snapshot, stop):
        while not stop():
            threading.Event().wait(0.005)
        stopped.set()
        return {"run_id": "late"}
    a = jobs.start(obedient)
    jobs.start(obedient)
    jobs.cancel(a)
    assert stopped.wait(2), "the search must notice the stop and end"
    for _ in range(200):
        if jobs.running() == 1:
            break
        threading.Event().wait(0.01)
    assert jobs.running() == 1  # the cancelled thread is gone; its slot is free
    c = jobs.start(lambda on_snapshot, stop: {"run_id": "c"})
    assert jobs.get(a)["status"] == "cancelled" and jobs.get(a)["run"] is None  # the late result was dropped
    assert wait_done(jobs, c)["status"] == "done"
    jobs.cancel(jobs.get(c) and [j for j in jobs._jobs][1])


def test_the_cap_counts_work_that_is_still_running_after_a_cancel():
    release = threading.Event()
    jobs = PlanJobs(Settings(area_thread=True, plan_jobs=1))
    a = jobs.start(lambda on_snapshot, stop: release.wait(5) or {"run_id": "a"})  # ignores stop: still running
    jobs.cancel(a)
    with pytest.raises(Busy):
        jobs.start(lambda on_snapshot, stop: {"run_id": "b"})
    release.set()


def test_a_run_slot_is_shared_with_requests_that_plan_inline():
    jobs = PlanJobs(Settings(area_thread=False, plan_jobs=1))
    with jobs.slot():
        with pytest.raises(Busy):
            jobs.start(lambda on_snapshot, stop: {"run_id": "x"})
    assert jobs.get(jobs.start(lambda on_snapshot, stop: {"run_id": "x"}))["status"] == "done"
