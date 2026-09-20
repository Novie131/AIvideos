"""序列佇列：16GB 機器一次只能跑一個重任務，所以 worker 固定 1 個。"""
from __future__ import annotations
import asyncio, itertools, time, traceback
from typing import Any, Awaitable, Callable

_ids = itertools.count(1)


class Job:
    def __init__(self, kind: str, label: str, fn: Callable[["Job"], Awaitable[Any]]):
        self.id = f"j{next(_ids)}"
        self.kind, self.label, self.fn = kind, label, fn
        self.status = "queued"          # queued | running | done | error | cancelled
        self.steps: list[dict] = []
        self.result: Any = None
        self.error: str | None = None
        self.created = time.time()
        self.started: float | None = None
        self.ended: float | None = None

    def dict(self) -> dict:
        el = (self.ended or time.time()) - (self.started or self.created)
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "status": self.status, "steps": self.steps, "error": self.error,
                "elapsed": round(el, 1), "result": self.result}


class Queue:
    def __init__(self):
        self.q: asyncio.Queue[Job] = asyncio.Queue()
        self.jobs: dict[str, Job] = {}
        self.subs: set[asyncio.Queue] = set()
        self.current: Job | None = None
        self._worker: asyncio.Task | None = None

    def start(self):
        if not self._worker:
            self._worker = asyncio.create_task(self._run())

    def submit(self, kind: str, label: str, fn) -> Job:
        job = Job(kind, label, fn)
        self.jobs[job.id] = job
        self.q.put_nowait(job)
        self.broadcast({"type": "job", "job": job.dict()})
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.status == "queued":
            job.status = "cancelled"
            self.broadcast({"type": "job", "job": job.dict()})
            return True
        return False

    def step(self, job: Job, text: str, pct: float | None = None):
        job.steps.append({"t": round(time.time() - (job.started or job.created), 1),
                          "text": text, "pct": pct})
        self.broadcast({"type": "job", "job": job.dict()})

    def broadcast(self, event: dict):
        for s in list(self.subs):
            try:
                s.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _run(self):
        while True:
            job = await self.q.get()
            if job.status == "cancelled":
                continue
            self.current, job.status, job.started = job, "running", time.time()
            self.broadcast({"type": "job", "job": job.dict()})
            try:
                job.result = await job.fn(job)
                job.status = "done"
            except Exception as e:
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            finally:
                job.ended = time.time()
                self.current = None
                self.broadcast({"type": "job", "job": job.dict()})
            self.q.task_done()


QUEUE = Queue()
