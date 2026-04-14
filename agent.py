"""Single-file Harbor agent harness: --agent-import-path agent:AutoAgent."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from agents import Agent, Runner, function_tool
from agents.items import (
    ItemHelpers,
    MessageOutputItem,
    ReasoningItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.tool import FunctionTool
from agents.usage import Usage
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


# ============================================================================
# EDITABLE HARNESS — prompt, tools, agent construction
# ============================================================================

SYSTEM_PROMPT = "You are an agent that executes tasks"
MODEL = "gpt-5"
MAX_TURNS = 30
CODEX_REASONING_EFFORT = "high"


@dataclass
class LocalRunResult:
    new_items: list[object]
    raw_responses: list[SimpleNamespace]
    last_response_id: str | None = None


def create_tools(environment: BaseEnvironment) -> list[FunctionTool]:
    """Create tools for the agent. Add new tools here."""

    @function_tool
    async def run_shell(command: str) -> str:
        """Run a shell command in the task environment. Returns stdout and stderr."""
        try:
            result = await environment.exec(command=command, timeout_sec=120)
            out = ""
            if result.stdout:
                out += result.stdout
            if result.stderr:
                out += f"\nSTDERR:\n{result.stderr}" if out else f"STDERR:\n{result.stderr}"
            return out or "(no output)"
        except Exception as exc:
            return f"ERROR: {exc}"

    return [run_shell]


def create_agent(environment: BaseEnvironment) -> Agent:
    """Build the agent. Modify to add handoffs, sub-agents, or agent-as-tool."""
    tools = create_tools(environment)
    return Agent(
        name="autoagent",
        instructions=SYSTEM_PROMPT,
        tools=tools,
        model=MODEL,
    )


def choose_backend() -> str:
    """Select an execution backend for the task run."""
    explicit = os.getenv("AUTOAGENT_BACKEND")
    if explicit:
        return explicit.lower()
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if shutil.which("codex"):
        return "codex"
    return "openai"


def build_codex_prompt(instruction: str) -> str:
    """Explain how the Harbor task container is mirrored into the local Codex workspace."""
    normalized_instruction = instruction.replace("/app/", "./app/")
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "You are operating on a local mirror of a Harbor task container.\n"
        "- The container path `/app` is mirrored locally at `./app`.\n"
        "- When the task mentions `/app/...`, use `./app/...` in this workspace.\n"
        "- Prefer changing only the files needed to complete the task.\n"
        "- Finish only when the requested artifacts exist under `./app`.\n\n"
        "Original task instruction:\n"
        f"{instruction}\n\n"
        "Normalized local instruction:\n"
        f"{normalized_instruction}\n"
    )


def parse_codex_usage(stdout_text: str) -> tuple[str | None, Usage]:
    """Extract thread id and token usage from Codex JSONL output."""
    thread_id = None
    usage = Usage()

    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "turn.completed":
            event_usage = event.get("usage", {})
            input_tokens = int(event_usage.get("input_tokens", 0) or 0)
            cached_input_tokens = int(event_usage.get("cached_input_tokens", 0) or 0)
            output_tokens = int(event_usage.get("output_tokens", 0) or 0)
            usage.requests += 1
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.total_tokens += input_tokens + output_tokens
            usage.input_tokens_details.cached_tokens += cached_input_tokens

    return thread_id, usage


async def run_openai_task(
    environment: BaseEnvironment,
    instruction: str,
) -> tuple[object, int]:
    """Run the original OpenAI Agents SDK harness."""
    agent = create_agent(environment)
    t0 = time.time()
    result = await Runner.run(agent, input=instruction, max_turns=MAX_TURNS)
    duration_ms = int((time.time() - t0) * 1000)
    return result, duration_ms


async def run_codex_task(
    environment: BaseEnvironment,
    instruction: str,
) -> tuple[LocalRunResult, int]:
    """Mirror /app locally, let Codex operate on it, then sync the result back."""
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError(
            "AUTOAGENT_BACKEND=codex was requested, but `codex` is not available on PATH."
        )

    workspace_root = (Path(environment.trial_paths.agent_dir) / "codex_workspace").resolve()
    app_root = workspace_root / "app"
    prompt_path = workspace_root / "TASK.md"
    stdout_path = workspace_root / "codex.stdout.jsonl"
    stderr_path = workspace_root / "codex.stderr.log"
    last_message_path = workspace_root / "codex.last_message.txt"

    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    app_root.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(build_codex_prompt(instruction))

    await environment.exec(command="mkdir -p /app /app/output", timeout_sec=30)

    replace_remote_app = False
    try:
        await environment.download_dir(source_dir="/app", target_dir=app_root)
        replace_remote_app = True
    except Exception:
        app_root.mkdir(parents=True, exist_ok=True)

    command = [
        codex_path,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--json",
        "-c",
        f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
        "-m",
        MODEL,
        "-C",
        str(workspace_root),
        "-o",
        str(last_message_path),
        prompt_path.read_text(),
    ]

    t0 = time.time()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(workspace_root),
        env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    duration_ms = int((time.time() - t0) * 1000)

    stdout_text = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
    stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
    stdout_path.write_text(stdout_text)
    stderr_path.write_text(stderr_text)

    if process.returncode != 0:
        raise RuntimeError(
            "Codex execution failed.\n"
            f"Command: {' '.join(command)}\n"
            f"Stdout:\n{stdout_text}\n"
            f"Stderr:\n{stderr_text}"
        )

    if replace_remote_app:
        await environment.exec(
            command="find /app -mindepth 1 -maxdepth 1 -exec rm -rf {} +",
            timeout_sec=120,
        )
    else:
        await environment.exec(command="mkdir -p /app", timeout_sec=30)
    await environment.upload_dir(source_dir=app_root, target_dir="/app")

    thread_id, usage = parse_codex_usage(stdout_text)
    raw_responses = []
    if usage.requests or usage.total_tokens:
        raw_responses.append(SimpleNamespace(usage=usage))

    return LocalRunResult(
        new_items=[],
        raw_responses=raw_responses,
        last_response_id=thread_id,
    ), duration_ms


async def run_task(
    environment: BaseEnvironment,
    instruction: str,
) -> tuple[object, int]:
    """Run the task through either OpenAI Agents SDK or Codex CLI."""
    backend = choose_backend()
    if backend == "codex":
        return await run_codex_task(environment, instruction)
    if backend == "openai":
        return await run_openai_task(environment, instruction)
    raise RuntimeError(f"Unsupported AUTOAGENT_BACKEND value: {backend}")


# ============================================================================
# FIXED ADAPTER BOUNDARY: do not modify unless the human explicitly asks.
# Harbor integration and trajectory serialization live here.
# ============================================================================

def to_atif(result: object, model: str, duration_ms: int = 0) -> dict:
    """Convert OpenAI Agents SDK RunResult to an ATIF trajectory dict."""
    steps: list[dict] = []
    step_id = 0
    now = datetime.now(timezone.utc).isoformat()

    def _step(source: str, message: str, **extra: object) -> dict:
        nonlocal step_id
        step_id += 1
        step = {
            "step_id": step_id,
            "timestamp": now,
            "source": source,
            "message": message,
        }
        step.update({key: value for key, value in extra.items() if value is not None})
        return step

    pending_tool_call = None
    for item in result.new_items:
        if isinstance(item, MessageOutputItem):
            text = ItemHelpers.text_message_output(item)
            if text:
                steps.append(_step("agent", text, model_name=model))
        elif isinstance(item, ReasoningItem):
            summaries = getattr(item.raw_item, "summary", None)
            reasoning = "\n".join(s.text for s in summaries if hasattr(s, "text")) if summaries else None
            if reasoning:
                steps.append(
                    _step(
                        "agent",
                        "(thinking)",
                        reasoning_content=reasoning,
                        model_name=model,
                    )
                )
        elif isinstance(item, ToolCallItem):
            raw = item.raw_item
            if hasattr(raw, "name"):
                pending_tool_call = raw
        elif isinstance(item, ToolCallOutputItem) and pending_tool_call:
            arguments = (
                json.loads(pending_tool_call.arguments)
                if isinstance(pending_tool_call.arguments, str)
                else pending_tool_call.arguments
            )
            output_str = str(item.output) if item.output else ""
            steps.append(
                _step(
                    "agent",
                    f"Tool: {pending_tool_call.name}",
                    tool_calls=[
                        {
                            "tool_call_id": pending_tool_call.call_id,
                            "function_name": pending_tool_call.name,
                            "arguments": arguments,
                        }
                    ],
                    observation={
                        "results": [
                            {
                                "source_call_id": pending_tool_call.call_id,
                                "content": output_str,
                            }
                        ]
                    },
                )
            )
            pending_tool_call = None

    if pending_tool_call:
        arguments = (
            json.loads(pending_tool_call.arguments)
            if isinstance(pending_tool_call.arguments, str)
            else pending_tool_call.arguments
        )
        steps.append(
            _step(
                "agent",
                f"Tool: {pending_tool_call.name}",
                tool_calls=[
                    {
                        "tool_call_id": pending_tool_call.call_id,
                        "function_name": pending_tool_call.name,
                        "arguments": arguments,
                    }
                ],
            )
        )

    if not steps:
        steps.append(_step("user", "(empty)"))

    usage = Usage()
    for response in result.raw_responses:
        usage.add(response.usage)

    return {
        "schema_version": "ATIF-v1.6",
        "session_id": getattr(result, "last_response_id", None) or "unknown",
        "agent": {"name": "autoagent", "version": "0.1.0", "model_name": model},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": usage.input_tokens,
            "total_completion_tokens": usage.output_tokens,
            "total_cached_tokens": getattr(usage.input_tokens_details, "cached_tokens", 0) or 0,
            "total_cost_usd": None,
            "total_steps": len(steps),
            "extra": {"duration_ms": duration_ms, "num_turns": len(result.raw_responses)},
        },
    }


class AutoAgent(BaseAgent):
    """Harbor agent adapter. Runs the OpenAI agent host-side and proxies shell into the container."""

    SUPPORTS_ATIF = True

    def __init__(self, *args, extra_env: dict[str, str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra_env = dict(extra_env) if extra_env else {}

    @staticmethod
    def name() -> str:
        return "autoagent"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await environment.exec(command="mkdir -p /task")
        instr_file = self.logs_dir / "instruction.md"
        instr_file.write_text(instruction)
        await environment.upload_file(source_path=instr_file, target_path="/task/instruction.md")

        result, duration_ms = await run_task(environment, instruction)

        atif = to_atif(result, model=MODEL, duration_ms=duration_ms)
        traj_path = self.logs_dir / "trajectory.json"
        traj_path.write_text(json.dumps(atif, indent=2))

        try:
            final_metrics = atif.get("final_metrics", {})
            context.n_input_tokens = final_metrics.get("total_prompt_tokens", 0)
            context.n_output_tokens = final_metrics.get("total_completion_tokens", 0)
            context.n_cache_tokens = final_metrics.get("total_cached_tokens", 0)
        except Exception:
            pass

        usage = Usage()
        for response in result.raw_responses:
            usage.add(response.usage)
        print(
            f"turns={len(result.raw_responses)} duration_ms={duration_ms} "
            f"input={usage.input_tokens} output={usage.output_tokens}"
        )


__all__ = ["AutoAgent"]
