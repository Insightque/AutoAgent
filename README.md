# AutoAgent

AutoAgent is a Harbor-compatible harness for benchmark-driven agent engineering.
You give it benchmark tasks, it runs an autonomous coding agent against those
tasks, and Harbor records deterministic scores for each run.

![teaser](progress.png)

The core idea is simple:

- Humans define the optimization goal in `program.md`
- The runnable harness lives in `agent.py`
- Harbor provides the benchmark runner, environment isolation, and verifier flow
- Task verifiers write deterministic rewards so regressions are easy to detect

This fork is set up to work with the local `codex` CLI by default, so it can run
without an `OPENAI_API_KEY` as long as the machine is already logged into Codex.

## What This Can Automate

AutoAgent is useful anywhere you want an agent to work on reproducible tasks and
be judged automatically. Typical examples:

- Infra smoke checks that verify the benchmark loop itself is wired correctly
- Code repair tasks such as fixing a Python bug and rerunning `pytest`
- Data transformation tasks such as converting CSV input into exact JSON output
- Regression suites for an autonomous coding agent
- Agent harness experiments where prompt, tooling, or orchestration changes are
  evaluated by score instead of by intuition
- Overnight hill-climbing loops where a meta-agent iterates on the harness and
  keeps only changes that improve benchmark results

## How It Works

The important files are:

- `agent.py`: single-file runnable harness under test
- `program.md`: instructions for the meta-agent that improves the harness
- `tasks/`: Harbor-format benchmark tasks
- `Dockerfile.base`: shared base image for task environments
- `jobs/`: Harbor job outputs, verifier logs, and trajectories

At runtime:

1. Harbor loads a task from `tasks/`
2. Harbor builds the task environment from `environment/Dockerfile`
3. `agent:AutoAgent` receives the task instruction
4. The harness runs either:
   - the OpenAI Agents SDK backend when `OPENAI_API_KEY` is set, or
   - the local `codex` CLI backend when `AUTOAGENT_BACKEND=codex` or no OpenAI
     key is present
5. The agent reads files, runs commands, edits outputs, and finishes
6. The task verifier runs deterministic checks and writes `/logs/verifier/reward.txt`
7. Harbor aggregates rewards into per-task and per-job scores

For the Codex backend, the Harbor container's `/app` directory is mirrored into a
local workspace, `codex exec` works against that mirror, and the modified files
are synced back into the task container before the verifier runs.

## Included Benchmark Tasks

This repository already includes three representative tasks:

- `infra-smoke-edit`: edits a small text file and writes an exact output file
- `python-bugfix`: fixes an off-by-one bug and passes deterministic `pytest`
- `csv-transform`: reads CSV input and produces an exact JSON summary

These tasks validate that the full loop works:

- task loading
- shell execution
- file editing and output creation
- deterministic verifier scoring
- Harbor result aggregation

## Quick Start

Requirements:

- Python 3.12
- Docker
- `uv`
- local `codex` CLI installed and logged in, or a valid `OPENAI_API_KEY`

Setup:

```bash
# 1. Install dependencies
uv sync

# 2. Prepare local environment config
cp .env.example .env

# 3. Build the shared base image
docker build -f Dockerfile.base -t autoagent-base .

# 4. Check the Harbor agent import path
uv run python -c "from agent import AutoAgent; print(f'{AutoAgent.__module__}:{AutoAgent.__name__}')"
```

Run a single task:

```bash
rm -rf jobs
mkdir -p jobs
uv run harbor run -p tasks/ -i "infra-smoke-edit" -l 1 -n 1 \
  --agent-import-path agent:AutoAgent -o jobs --job-name latest > run.log 2>&1
```

Run the full representative benchmark:

```bash
rm -rf jobs
mkdir -p jobs
uv run harbor run -p tasks/ -n 3 \
  --agent-import-path agent:AutoAgent -o jobs --job-name latest > run.log 2>&1
```

Results are written to:

- `run.log`
- `jobs/<job-name>/result.json`
- `jobs/<job-name>/<trial-name>/verifier/reward.txt`
- `jobs/<job-name>/<trial-name>/agent/trajectory.json`

## Backend Selection

Backend selection is automatic:

- if `OPENAI_API_KEY` is set, the harness uses the OpenAI Agents SDK backend
- otherwise, if `codex` is available on `PATH`, the harness uses the Codex backend
- you can override this with `AUTOAGENT_BACKEND=codex` or `AUTOAGENT_BACKEND=openai`

Recommended local setup:

```bash
codex login status
cat .env
```

Example `.env`:

```bash
OPENAI_API_KEY=
AUTOAGENT_BACKEND=codex
```

## Running the Meta-Agent

The benchmark harness and the meta-agent loop are intentionally separated.
To run harness-improvement iterations, point a coding agent at this repo and use
the instructions in `program.md`.

Example prompt:

```text
Read program.md and let's kick off a new experiment!
```

The meta-agent should modify only the editable section of `agent.py`. The fixed
Harbor adapter boundary must remain untouched unless a human explicitly asks for
changes there.

## Task Format

Tasks follow Harbor's local task structure:

```text
tasks/my-task/
  task.toml
  instruction.md
  environment/
    Dockerfile
  tests/
    test.sh
    test.py
  files/
    input assets used by the task
```

In this repo, the `files/` directory is committed as task reference data, and the
task `environment/Dockerfile` places the runnable copies under `/app/files` so
the baseline harness can access them directly.

## Project Structure

```text
agent.py
program.md
Dockerfile.base
.env.example
tasks/
jobs/
run.log
progress.png
```

## Design Principles

- Benchmark first: trust task scores over intuition
- Keep the harness simple: most behavior lives in one file
- Preserve the Harbor adapter boundary: edit only the intended section
- Prefer deterministic verifiers for smoke and capability tests
- Use Docker isolation so task execution cannot damage the host system

## Cleanup

```bash
uv run harbor cache clean -f
docker container prune -f
docker system prune -a -f
```

If Docker becomes unhealthy:

```bash
killall Docker && open -a Docker
```

## License

MIT
