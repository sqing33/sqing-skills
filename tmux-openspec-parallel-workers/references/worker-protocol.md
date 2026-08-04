# Worker Protocol Reference

## Matrix Design

Create one worker for each independently implementable slice. Prefer file or module ownership over vague feature ownership.

The parent agent chooses the worker count by the number of entries in `workers`. Optional top-level `worker_count` and CLI `--workers N` are consistency checks; when present, each must equal `workers.length`. The hard limit is 10 workers.

Required worker fields:

- `id`: stable worker id such as `w01`; only letters, digits, dot, underscore, and hyphen.
- `title`: short task title.
- `scope`: implementation boundary in human language.
- `ownership`: non-empty array of paths or glob-like ownership descriptions.
- `depends_on`: array of worker ids that must finish first.
- `acceptance`: concrete evidence expected from this worker.
- `verify_cmd`: optional worker-specific verification command; falls back to the matrix-level command.

Rules:

- Dependencies must reference existing worker ids and cannot self-reference.
- Shared contracts, types, schemas, and migrations should be either a first wave dependency or owned by exactly one worker.
- UI and backend workers may run in parallel only when the API contract is already specified in OpenSpec.
- Do not put speculative tasks in the matrix. The parent agent should finish OpenSpec planning before `draft`.

## Worker Prompt Contract

Each child worker receives:

- Global goal.
- OpenSpec change name and selected OpenSpec JSON context.
- The exact matrix entry for that worker.
- Branch, worktree, ownership, dependencies, acceptance, and verify command.
- A strict summary marker format for machine parsing.

Each child worker must:

- Read the OpenSpec change files before editing implementation code.
- Treat OpenSpec as the contract and stop on contract conflict.
- Stay on the assigned branch and worktree.
- Avoid tmux orchestration and worker spawning.
- Avoid manual git commits. The orchestrator commits successful worker changes after checking status and ownership.
- Report `done`, `blocked`, or `failed` honestly.

## Git Handoff And Commit Protocol

Borrow these conventions for worker worktree handoff:

- Read `git status --short` before committing, merging, or splitting work.
- Keep each worker commit to one clear intent, scoped to the worker's ownership.
- Preserve unrelated user or other-agent changes. Do not clean up, format, revert, or restage files outside the worker's assignment.
- Stage precisely by ownership pathspecs. Do not use broad `git add .` for implementation workers.
- Record `git status --short`, `git diff --stat`, and `git diff --cached --stat` in the worker log before the auto-commit.
- Use commit messages shaped as `<type>(<scope>): <中文描述实际修改内容>`, for example `feat(cli): 增加任务文件读取错误提示`.
- Prefer the matrix `commit` field or the worker summary `commit:` field to name the actual change. Never use generic labels such as `完成 w02 工作结果`.
- Prefer commit types `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, or `perf`.
- Do not use vague messages such as `update`, `fix`, `wip`, `AI changes`, or `codex update`.
- Never run destructive Git commands unless the human explicitly asks: `git reset --hard`, force-push, `git rebase`, `git branch -D`, `git checkout -- .`, `git checkout -- <path>`, or `git clean -fd/x`.
- If an ownership boundary edit is required, add that path to the matrix ownership before launching the worker, or expect the orchestrator commit guard to fail with uncommitted changes outside ownership.

Dependency handoff:

- Successful workers are auto-committed on their worker branches.
- A worker with `depends_on` starts from its own branch, then the orchestrator merges each completed dependency branch before launching Claude Code.
- If dependency merge conflicts occur, the parent should revise the matrix or start an integration/fix worker rather than hiding conflicts.

## Wait And State Refresh

- The source of truth is still file/git state: `.done` exit-code files, worker result Markdown, tmux pane liveness, state JSON, and worker branch commits.
- `wait` is a blocking foreground command, not a daemon. If it is interrupted, run it again with the same `--run` id.
- Use `wait --run <id>` to wait for the current wave to finish.
- Use `wait --run <id> --next-wave --until all-done` to automatically launch dependency waves until all implementation workers are `done`.
- Use `wait --run <id> --next-wave --auto-integrate --until complete` to launch dependency waves, start the merge worker after implementation workers finish, and wait for integration.
- `wait` exits non-zero on failed/blocked workers, stalled dependency progress, or timeout.

## Recommended Split Patterns

- `contract-first`: one early worker owns shared types, API contracts, schema/migration files, or generated clients; other workers depend on it.
- `vertical-slices`: independent product areas such as `auth`, `billing`, `admin-ui`, and `worker-service`.
- `backend-frontend-parallel`: backend and frontend workers run together only after OpenSpec has frozen request/response shapes.
- `tests-docs-late`: test/docs workers depend on implementation workers unless they only add scaffolding.

## Merge Worker

The merge worker:

- Starts from the original base branch in `orchestrator/<run_id>/integrate`.
- Merges completed worker branches.
- Resolves conflicts with the OpenSpec contract as the source of truth.
- Runs the full matrix `verify_cmd`.
- Does not archive the OpenSpec change.
- Lets the orchestrator auto-commit successful integration fixes after final status/diff checks.
