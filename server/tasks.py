"""Single-process task lifecycle with cooperative cancellation and atomic publication."""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    session: int | None
    state: str = "running"
    event: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.monotonic)


class TaskService:
    def __init__(self):
        self.lock = threading.RLock()
        self.tasks = {}
        self.busy = set()

    def _prune(self):
        now = time.monotonic()
        self.tasks = {
            k: v
            for k, v in self.tasks.items()
            if v.state == "running" or now - v.created < 600
        }
        if len(self.tasks) >= 1000:
            raise ValueError("任务过多，请稍后重试")

    def start(self, key, session):
        with self.lock:
            self._prune()
            old = self.tasks.get(key)
            if old:
                if old.event.is_set():
                    raise InterruptedError("任务已取消")
                raise ValueError("taskId 已使用，请勿重复提交")
            if session and session in self.busy:
                raise ValueError("该会话仍有任务运行，请稍后重试")
            task = Task(key, session)
            self.tasks[key] = task
            if session:
                self.busy.add(session)
            return task

    def cancel(self, key):
        with self.lock:
            if key not in self.tasks:
                self._prune()
                self.tasks[key] = Task(key, None, "cancelled")
            task = self.tasks[key]
            if task.state in ("running", "cancelled"):
                task.event.set()
                task.state = "cancelled"
            return {
                "taskId": key,
                "state": task.state,
                "stopped": task.state == "cancelled",
            }

    def publish(self, task, callback):
        with self.lock:
            if task.event.is_set():
                raise InterruptedError("任务已取消")
            value = callback()
            task.state = "completed"
            return value

    def finish(self, task):
        with self.lock:
            if task.state == "running":
                task.state = "failed"
            self.busy.discard(task.session)

    def status(self, key):
        with self.lock:
            task = self.tasks.get(key)
            return {"taskId": key, "state": task.state} if task else None


TASKS = TaskService()
