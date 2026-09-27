from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import time
import uuid
from collections.abc import Callable

from .config import Config, Project
from .state import State


class JobContext:
    def __init__(self, job: dict, project: Project, config: Config, state: State):
        self.job, self.project, self.config, self.state = job, project, config, state
        self.cancelled = asyncio.Event()
        self.cancel_reason = "cancelled"
        self.requests: dict[str, tuple[str, asyncio.Future]] = {}
        self.request_kinds: dict[str, str] = {}
        self.provider_id = state.session(
            job["session_id"], job["owner_id"], job["project_id"], job["runtime"]
        )

    def emit(self, kind: str, data: dict):
        # Stop producing events rather than silently discarding unacknowledged results.
        if self.state.pending_count() >= self.config.max_outbox_events:
            self.cancel("outbox_full")
            raise RuntimeError("Event outbox full; restore Hub connectivity")
        self.state.emit(self.job["id"], kind, data)

    def save_session(self, provider_id: str):
        if not provider_id or not isinstance(provider_id, str):
            raise ValueError("Invalid runtime session ID")
        self.provider_id = provider_id
        self.state.save_session(
            self.job["session_id"],
            self.job["owner_id"],
            self.job["project_id"],
            self.job["runtime"],
            provider_id,
        )
        self.emit("session", {"session_id": self.job["session_id"]})

    def cancel(self, reason: str):
        self.cancel_reason = reason
        self.cancelled.set()
        for _, future in self.requests.values():
            if not future.done():
                future.set_result({"decision": "deny"})

    async def ask(self, kind: str, details: dict) -> dict:
        if self.cancelled.is_set():
            return {"decision": "deny"}
        if len(json.dumps(details)) > 12000:
            self.emit("request_rejected", {"reason": "request_too_large_for_messenger"})
            return {"decision": "deny"}
        identifier = str(uuid.uuid4())
        digest = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
        future = asyncio.get_running_loop().create_future()
        self.requests[identifier] = (digest, future)
        self.request_kinds[identifier] = "approval" if kind == "approval_required" else "input"
        self.emit(
            kind,
            {
                "request_id": identifier,
                "digest": digest,
                "expires_at": time.time() + self.config.approval_timeout_seconds,
                "details": details,
            },
        )
        try:
            return await asyncio.wait_for(future, self.config.approval_timeout_seconds)
        except TimeoutError:
            return {"decision": "deny"}
        finally:
            self.requests.pop(identifier, None)
            self.request_kinds.pop(identifier, None)
            self.emit("request_closed", {"request_id": identifier})

    def control(self, control: dict) -> bool:
        if (
            control.get("job_id") != self.job["id"]
            or control.get("owner_id") != self.job["owner_id"]
        ):
            return False
        if control.get("type") == "cancel":
            self.cancel("user_cancelled")
            return True
        entry = self.requests.get(control.get("request_id"))
        if not entry or entry[0] != control.get("digest") or entry[1].done():
            return False
        if control.get("type") != self.request_kinds.get(control.get("request_id")):
            return False
        # Remote callers cannot replace tool arguments or grant persistent permissions.
        entry[1].set_result(
            {"decision": control.get("decision", "deny"), "answers": control.get("answers", {})}
        )
        return True


async def stop_process(process: asyncio.subprocess.Process):
    if process.returncode is not None and os.name == "nt":
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill.exe",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            pass
        # Also kill descendants if the parent exited first.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


class CodexRuntime:
    """Codex app-server JSONL over local pipes; never opens a network listener."""

    def __init__(self, command: list[str]):
        self.command = command
        self.process = None
        self.sequence = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.handlers: dict[object, asyncio.Task] = {}
        self.done = None
        self.ctx = None
        self.thread_id = None
        self.turn_id = None
        self.write_lock = asyncio.Lock()
        self.final_text = ""

    async def send(self, message: dict):
        async with self.write_lock:
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()

    async def call(self, method: str, params: dict) -> dict:
        self.sequence += 1
        identifier = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            await self.send({"id": identifier, "method": method, "params": params})
            return await asyncio.wait_for(future, 30)
        finally:
            self.pending.pop(identifier, None)

    async def server_request(self, message: dict):
        method, params = message["method"], message.get("params", {})
        response = None
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if self.ctx.project.policy == "read-only":
                decision = "decline"
            else:
                answer = await self.ctx.ask("approval_required", {"method": method, **params})
                decision = "accept" if answer.get("decision") == "allow" else "decline"
                choices = params.get("availableDecisions")
                if choices is not None and decision not in choices:
                    decision = "decline"
            response = {"decision": decision}
        elif method == "item/tool/requestUserInput":
            answer = await self.ctx.ask(
                "input_required", {"questions": params.get("questions", [])}
            )
            values = answer.get("answers", {})
            if not isinstance(values, dict):
                values = {}
            response = {
                "answers": {
                    q["id"]: {"answers": [str(v) for v in values.get(q["id"], [])]}
                    for q in params.get("questions", [])
                    if isinstance(values.get(q["id"], []), list)
                }
            }
        elif method == "item/permissions/requestApproval":
            # No dynamic filesystem/network widening from messenger clients.
            response = {"permissions": {}, "scope": "turn"}
        elif method == "mcpServer/elicitation/request":
            response = {"action": "decline", "content": None}
        if response is None:
            await self.send(
                {
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Unsupported client request"},
                }
            )
        else:
            await self.send({"id": message["id"], "result": response})

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "id" in message and "method" not in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError("Codex rejected a protocol request"))
                        else:
                            future.set_result(message.get("result", {}))
                elif "id" in message:
                    task = asyncio.create_task(self.server_request(message))
                    self.handlers[message["id"]] = task
                    task.add_done_callback(self._handler_done)
                else:
                    self.notification(message.get("method"), message.get("params", {}))
            if not self.done.done():
                raise RuntimeError("Codex exited before turn completion")
        except Exception as exc:
            if not self.done.done():
                self.done.set_exception(exc)
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Codex connection closed"))

    def _handler_done(self, task):
        if not task.cancelled() and task.exception() and not self.done.done():
            self.done.set_exception(task.exception())

    def notification(self, method: str, params: dict):
        if method == "serverRequest/resolved":
            task = self.handlers.get(params.get("requestId"))
            if task and not task.done():
                task.cancel()
        elif method == "turn/started":
            self.turn_id = params["turn"]["id"]
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                self.final_text = item.get("text", "")
                self.ctx.emit("message", {"text": self.final_text})
            elif item.get("type") in {"commandExecution", "fileChange", "mcpToolCall"}:
                self.ctx.emit("progress", {"operation": item["type"], "status": item.get("status")})
        elif method == "turn/completed" and not self.done.done():
            status = params.get("turn", {}).get("status")
            if status == "completed":
                self.done.set_result({"text": self.final_text})
            else:
                self.done.set_exception(RuntimeError(f"Codex turn ended: {status}"))

    async def run(self, ctx: JobContext) -> dict:
        self.ctx = ctx
        self.done = asyncio.get_running_loop().create_future()
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            "app-server",
            cwd=ctx.project.path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=2 * 1024 * 1024,
            start_new_session=os.name != "nt",
        )
        reader = asyncio.create_task(self.read())
        try:
            await self.call(
                "initialize", {"clientInfo": {"name": "citrus_agent", "version": "0.1.0"}}
            )
            await self.send({"method": "initialized", "params": {}})
            params = {
                "cwd": ctx.project.path,
                "sandbox": (
                    "read-only" if ctx.project.policy == "read-only" else "workspace-write"
                ),
                "approvalPolicy": "never" if ctx.project.policy == "read-only" else "on-request",
            }
            if ctx.provider_id:
                params["threadId"] = ctx.provider_id
            result = await self.call("thread/resume" if ctx.provider_id else "thread/start", params)
            self.thread_id = result["thread"]["id"]
            ctx.save_session(self.thread_id)
            result = await self.call(
                "turn/start",
                {
                    "threadId": self.thread_id,
                    "input": [{"type": "text", "text": ctx.job["prompt"]}],
                },
            )
            self.turn_id = result["turn"]["id"]
            return await self.done
        finally:
            if ctx.cancelled.is_set() and self.thread_id and self.turn_id:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self.call(
                            "turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}
                        ),
                        3,
                    )
            for task in self.handlers.values():
                task.cancel()
            await asyncio.gather(*self.handlers.values(), return_exceptions=True)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await stop_process(self.process)
            if self.done.done() and not self.done.cancelled():
                self.done.exception()  # Retrieve errors even when initialization failed first.


class ClaudeRuntime:
    def __init__(self, command: list[str] | None = None):
        self.command = command

    async def run(self, ctx: JobContext) -> dict:
        try:
            from claude_agent_sdk import (
                ClaudeAgentOptions,
                ClaudeSDKClient,
                HookMatcher,
                PermissionResultAllow,
                PermissionResultDeny,
            )
        except ImportError:
            raise RuntimeError("Install citrus-agent[claude] to enable Claude Code") from None
        from pathlib import Path

        async def check_tool(data, _tool_id, _context):
            tool = data.get("tool_name", "")
            args = data.get("tool_input", {})
            deny = None
            if ctx.project.policy == "read-only" and tool not in {
                "Read",
                "Glob",
                "Grep",
                "AskUserQuestion",
            }:
                deny = "Read-only project"
            root = Path(ctx.project.path).resolve()
            for field in ("file_path", "path"):
                if args.get(field):
                    path = Path(args[field])
                    resolved = (path if path.is_absolute() else root / path).resolve()
                    if not resolved.is_relative_to(root):
                        deny = "Path is outside the registered project"
            if tool in {"Edit", "Write", "Bash"} and not deny:
                answer = await ctx.ask("approval_required", {"tool": tool, "input": args})
                if answer.get("decision") != "allow":
                    deny = "User denied or approval expired"
                else:
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                        }
                    }
            if deny:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": deny,
                    }
                }
            return {}

        async def permission(tool, args, _context):
            if tool == "AskUserQuestion":
                answer = await ctx.ask("input_required", {"questions": args.get("questions", [])})
                values = answer.get("answers", {})
                if not isinstance(values, dict) or not values:
                    return PermissionResultDeny(message="No answer supplied")
                return PermissionResultAllow(updated_input={**args, "answers": values})
            if ctx.project.policy == "read-only":
                return PermissionResultDeny(message="Read-only project; additional access denied")
            answer = await ctx.ask("approval_required", {"tool": tool, "input": args})
            if answer.get("decision") == "allow":
                return PermissionResultAllow(updated_input=args)
            return PermissionResultDeny(message="Denied or expired")

        if self.command and len(self.command) != 1:
            raise ValueError("Claude SDK requires a native CLI executable path")
        tools = ["Read", "Glob", "Grep", "AskUserQuestion"]
        if ctx.project.policy == "development":
            tools += ["Edit", "Write", "Bash"]
        options = ClaudeAgentOptions(
            cwd=ctx.project.path,
            resume=ctx.provider_id,
            permission_mode="default",
            tools=tools,
            allowed_tools=[],
            setting_sources=[],
            strict_mcp_config=True,
            can_use_tool=permission,
            hooks={
                "PreToolUse": [
                    HookMatcher(
                        hooks=[check_tool], timeout=ctx.config.approval_timeout_seconds + 10
                    )
                ]
            },
            cli_path=self.command[0] if self.command else None,
        )
        # SDK connect/disconnect must run in the same task (AnyIO cancel scopes).
        async with ClaudeSDKClient(options=options) as client:
            try:
                await client.query(ctx.job["prompt"])
                async for message in client.receive_response():
                    name = type(message).__name__
                    if name == "SystemMessage" and message.subtype == "init":
                        ctx.save_session(message.data["session_id"])
                    elif name == "AssistantMessage":
                        for block in message.content:
                            if type(block).__name__ == "TextBlock":
                                ctx.emit("message", {"text": block.text})
                    elif name == "ResultMessage":
                        ctx.save_session(message.session_id)
                        if message.is_error:
                            raise RuntimeError("Claude task failed: " + message.subtype)
                        return {"text": message.result or "", "usage": message.usage}
                raise RuntimeError("Claude stream ended without a result")
            finally:
                if ctx.cancelled.is_set():
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(client.interrupt(), 3)


RuntimeFactory = Callable[[str, Config], object]


def make_runtime(name: str, config: Config):
    if name == "codex":
        return CodexRuntime(config.command("codex"))
    if name == "claude":
        return ClaudeRuntime(config.command("claude") if "claude" in config.commands else None)
    raise ValueError("Unsupported runtime")
