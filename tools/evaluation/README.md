# Isolated visual task evaluation

`run_batch.py` implements the root `CODEX_AGENT.md` protocol for all eleven tasks,
five scored episodes per task, seed zero. It uses the existing CPU bridge and
does not change task physics, rewards, success predicates, or native step limits.

Prepare and commit the plan, runner, evidence collector, and their tests before
initializing a batch. Initialization freezes their hashes, the complete guide,
the model settings, the observation strategy, and the interruption policy.

```bash
dexjoco-linux-cpu-20261012/dexjoco/.venv/bin/python tools/evaluation/run_batch.py \
  --batch-id 20261014-astra-medium \
  --credentials /absolute/path/to/authorized-credential-file.md \
  --base-url https://oneapi-comate.baidu-int.com/v1 \
  --initialize-only
```

Commit and push the initialized batch configuration on `test`, then run the same
command without `--initialize-only`. The credential file is read only in memory;
it is not copied into the report or exposed to the controller. Do not place a key
on the command line. The service must provide the exact `gpt-6-astra` model and
the Responses streaming protocol. No other model is substituted.

Each episode gets a new input history and a separate CPU runner process, Unix
socket, and log. The controller can only observe and act in that episode. It
has no file, shell, reset, task-selection, or previous-result tools. Up to three
different tasks run concurrently; trials of a given task remain sequential.
The current episode's complete history is retained in each request. Public
requests externalize image data URLs losslessly as files and SHA-256 references;
public responses and SSE events are recorded. Hidden reasoning and independent
backend versions are unavailable.

Each finished attempt is copied to
`reports/evaluations/<batch>/episodes/<task>/<attempt>/`, audited, indexed, committed,
and pushed to `test` before another attempt for that task starts. Other already
running tasks can finish while publication is serialized. An infrastructure,
evidence, or publication fault stops new scheduling and drains existing runs.
No strategy failures or model give-ups are replaced. Interruptions and any
replacements remain visible in both result tables.

The same command resumes a batch after a diagnosed transient infrastructure
problem. It checks the frozen configuration and skips recorded scored trials.
Do not edit the frozen controller or bridge to improve subsequent scores. An
interrupted scheduler with an unarchived attempt directory needs explicit
evidence recovery before resuming; never delete that directory to hide a run.
The scheduler holds a repository lock to prevent duplicate dispatch.

```bash
dexjoco-linux-cpu-20261012/dexjoco/.venv/bin/python \
  -W error::ResourceWarning -m unittest discover -s tools/evaluation -p 'test_*.py'
```

Unit fixtures and `reports/preflight/` results are infrastructure validation,
not scored robot episodes. Only actual official `succeed` flags determine task
success. Formal task rates require five scored trials; the overall rate requires
all fifty-five. The report keeps result completeness separate from evidence
completeness.
