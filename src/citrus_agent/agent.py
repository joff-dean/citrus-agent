from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
import random
import time
from pathlib import Path

from . import __version__
from .config import Config, validate_id
from .runtime import JobContext, make_runtime
from .state import State
from .transport import HubClient, HubError

log = logging.getLogger(__name__)


class Agent:
    def __init__(
        self, home: Path, config: Config, credentials: dict, hub=None, runtime_factory=make_runtime
    ):
        self.config = config
        self.credentials = credentials
        self.hub = hub or HubClient(config.hub_url, credentials["token"], config.ca_file)
        self.state = State(home, (credentials["token"],))
        self.prefix = f"/v1/agents/{validate_id(credentials['agent_id'])}"
        self.runtime_factory = runtime_factory
        self.active: JobContext | None = None
        self.active_task: asyncio.Task | None = None
        self.lease_deadline = 0.0
        self.stopping = asyncio.Event()
        self.fatal_error = None
        self.registered = False

    def stop(self, reason="agent_stopped"):
        self.stopping.set()
        if self.active:
            self.active.cancel(reason)

    def validate_job(self, job: dict):
        for key in ("id", "session_id", "project_id", "runtime"):
            validate_id(job[key])
        if job.get("owner_id") != self.credentials["owner_id"]:
            raise ValueError("Job owner does not match the enrolled user")
        if job.get("agent_id") != self.credentials["agent_id"]:
            raise ValueError("Job targets another agent")
        if not isinstance(job.get("prompt"), str) or not 1 <= len(job["prompt"]) <= 100000:
            raise ValueError("Prompt must contain 1–100000 characters")
        if not isinstance(job.get("lease_token"), str) or not job["lease_token"]:
            raise ValueError("Missing job lease")
        if not 30 <= job.get("lease_seconds", 0) <= 300:
            raise ValueError("Lease must be 30–300 seconds")
        project = self.config.projects.get(job["project_id"])
        if not project or job["runtime"] not in project.runtimes:
            raise ValueError("Project or runtime is not locally registered")
        project.validate()
        return project

    async def execute(self, ctx: JobContext):
        task = None
        cancel = None
        try:
            runtime = self.runtime_factory(ctx.job["runtime"], self.config)
            ctx.emit(
                "started", {"runtime": ctx.job["runtime"], "project_id": ctx.job["project_id"]}
            )
            task = asyncio.create_task(runtime.run(ctx))
            cancel = asyncio.create_task(ctx.cancelled.wait())
            done, _ = await asyncio.wait(
                {task, cancel},
                timeout=self.config.job_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done and not ctx.cancelled.is_set():
                result = task.result()
                self.state.finish(ctx.job["id"], "completed", result)
            else:
                if not ctx.cancelled.is_set():
                    ctx.cancel("job_timeout")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.state.finish(ctx.job["id"], "cancelled", {"reason": ctx.cancel_reason})
        except Exception as exc:
            # Exception text may contain model data, commands or provider credentials.
            self.state.finish(ctx.job["id"], "failed", {"error_type": type(exc).__name__})
            log.error("Job %s failed (%s)", ctx.job["id"], type(exc).__name__)
        finally:
            if cancel:
                cancel.cancel()
                await asyncio.gather(cancel, return_exceptions=True)
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.active = None

    async def claim_once(self):
        if self.active or self.state.pending_count() >= self.config.max_outbox_events:
            return
        result = await self.hub.post(
            self.prefix + "/jobs/claim",
            {
                "request_id": self.state.claim_id(),
                "wait_seconds": 20,
            },
        )
        job = result.get("job")
        if not job:
            self.state.clear_claim()
            return
        # Persist a job identity before clearing the idempotent claim request.
        validate_id(job["id"])
        if not self.state.start(job):
            self.state.emit(job["id"], "duplicate_ignored", {"retry_safe": False})
            self.state.clear_claim()
            return
        self.state.clear_claim()
        try:
            if self.stopping.is_set():
                raise ValueError("Agent is stopping")
            project = self.validate_job(job)
            ctx = JobContext(job, project, self.config, self.state)
        except (ValueError, KeyError, TypeError, OSError):
            self.state.finish(job["id"], "rejected", {"reason": "invalid_job_or_local_policy"})
            return
        self.lease_deadline = time.monotonic() + job["lease_seconds"]
        self.active = ctx
        self.active_task = asyncio.create_task(self.execute(ctx))

    async def heartbeat_once(self):
        active = self.active
        request = {
            "version": __version__,
            "platform": platform.system(),
            "name": self.config.name,
            "projects": {
                key: {"runtimes": p.runtimes, "policy": p.policy}
                for key, p in self.config.projects.items()
            },
            "capabilities": ["resume", "cancel", "approval", "user_input"],
            "active_job": (
                {"id": active.job["id"], "lease_token": active.job["lease_token"]}
                if active
                else None
            ),
        }
        result = await self.hub.post(self.prefix + "/heartbeat", request, timeout=10)
        self.registered = True
        if active and self.active is active:
            lease = result.get("lease") or {}
            if (
                lease.get("job_id") == active.job["id"]
                and lease.get("valid") is True
                and 30 <= lease.get("seconds", 0) <= 300
            ):
                self.lease_deadline = time.monotonic() + lease["seconds"]
            else:
                active.cancel("lease_rejected")
        for control in result.get("controls", []):
            identifier = validate_id(control["id"])
            if self.state.control_seen(identifier):
                continue
            accepted = bool(self.active and self.active.control(control))
            # A control acknowledgment is itself durable and delivered through the outbox.
            self.state.emit(
                control.get("job_id", "unknown"),
                "control_ack",
                {"control_id": identifier, "accepted": accepted},
            )
            self.state.mark_control(identifier)

    async def flush_once(self):
        events = self.state.pending()
        if events:
            result = await self.hub.post(self.prefix + "/events", {"events": events}, timeout=10)
            sent = {e["id"] for e in events}
            acknowledged = [i for i in result.get("acknowledged", []) if i in sent]
            self.state.acknowledge(acknowledged)

    async def loop(self, operation, interval: float):
        failures = 0
        while not self.stopping.is_set():
            try:
                await operation()
                failures = 0
            except HubError as exc:
                if exc.status in {401, 403, 410}:
                    self.fatal_error = f"Hub authorization revoked or rejected (HTTP {exc.status})"
                    self.stop("authorization_revoked")
                    break
                failures += 1
                log.warning("Hub unavailable (%s); retrying", exc.status)
            except (ValueError, KeyError, TypeError):
                self.fatal_error = "Hub protocol response is invalid"
                self.stop("protocol_error")
                break
            except Exception:
                self.fatal_error = "Unexpected local agent failure; check disk and configuration"
                self.stop("agent_error")
                break
            delay = interval if not failures else min(30, 2 ** min(failures, 5)) + random.random()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), delay)

    async def watchdog(self):
        while not self.stopping.is_set():
            if self.active and time.monotonic() >= self.lease_deadline:
                self.active.cancel("lease_expired")
            await asyncio.sleep(0.5)

    async def run(self):
        self.state.recover()
        tasks = []

        async def claim_when_registered():
            if self.registered:
                await self.claim_once()

        try:
            # Retry an offline Hub at startup, but never claim before authentication.
            tasks = [
                asyncio.create_task(self.loop(self.heartbeat_once, self.config.heartbeat_seconds)),
                asyncio.create_task(self.loop(self.flush_once, 1)),
                asyncio.create_task(self.loop(claim_when_registered, 1)),
                asyncio.create_task(self.watchdog()),
            ]
            await self.stopping.wait()
        finally:
            self.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.active_task:
                await self.active_task
            with contextlib.suppress(Exception):
                await self.flush_once()
            self.state.close()
        if self.fatal_error:
            raise RuntimeError(self.fatal_error)
