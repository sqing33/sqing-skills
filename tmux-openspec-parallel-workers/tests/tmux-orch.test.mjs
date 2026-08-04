import test from "node:test";
import assert from "node:assert/strict";

import {
  buildClaudeEnv,
  commitScopeFor,
  commitTypeFor,
  CmdError,
  MATRIX_SCHEMA,
  dependencyReadiness,
  normalizeAuthEnv,
  parseOrchMatrixData,
  parseSummary,
  selectChangeName,
  usage,
  waitAssessment,
  workerCommitMessage,
} from "../src/tmux-orch.mjs";

test("parseOrchMatrixData normalizes workers", () => {
  const matrix = parseOrchMatrixData({
    schema_version: MATRIX_SCHEMA,
    change: "add-api",
    goal: "Build API",
    worker_count: 2,
    verify_cmd: "npm test",
    workers: [
      {
        id: "w01",
        title: "Shared types",
        scope: "Create shared types",
        ownership: ["src/types/**"],
        acceptance: ["types compile"],
      },
      {
        id: "w02",
        title: "API routes",
        scope: "Implement routes",
        ownership: "src/api/**",
        depends_on: ["w01"],
      },
    ],
  }, { changeName: "add-api", goal: "Build API", expectedWorkers: "2" });

  assert.equal(matrix.schema_version, MATRIX_SCHEMA);
  assert.equal(matrix.worker_count, 2);
  assert.equal(matrix.workers.length, 2);
  assert.deepEqual(matrix.workers[1].ownership, ["src/api/**"]);
  assert.deepEqual(matrix.workers[1].depends_on, ["w01"]);
  assert.equal(matrix.workers[1].verify_cmd, "npm test");
});

test("parseOrchMatrixData rejects invalid dependencies", () => {
  assert.throws(() => parseOrchMatrixData({
    schema_version: MATRIX_SCHEMA,
    change: "bad",
    workers: [
      { id: "w01", title: "A", scope: "A", ownership: ["a"], depends_on: ["w99"] },
    ],
  }, { changeName: "bad" }), CmdError);
});

test("parseOrchMatrixData enforces worker count checks and limit", () => {
  const base = {
    schema_version: MATRIX_SCHEMA,
    change: "count-check",
    workers: [
      { id: "w01", title: "A", scope: "A", ownership: ["a"] },
      { id: "w02", title: "B", scope: "B", ownership: ["b"] },
    ],
  };
  assert.throws(() => parseOrchMatrixData({ ...base, worker_count: 1 }, { changeName: "count-check" }), CmdError);
  assert.throws(() => parseOrchMatrixData(base, { changeName: "count-check", expectedWorkers: "3" }), CmdError);
  assert.throws(() => parseOrchMatrixData({
    schema_version: MATRIX_SCHEMA,
    change: "too-many",
    workers: Array.from({ length: 11 }, (_, index) => ({
      id: `w${String(index + 1).padStart(2, "0")}`,
      title: "T",
      scope: "S",
      ownership: [`src/${index}`],
    })),
  }, { changeName: "too-many" }), CmdError);
});

test("Claude auth env supports gateway auth-token mode", () => {
  assert.equal(normalizeAuthEnv("auth_token"), "auth_token");
  assert.throws(() => normalizeAuthEnv("bad"), CmdError);

  const gatewayEnv = buildClaudeEnv({
    base_url: "https://gateway.example",
    api_key: "secret",
    model: "MiniMax-M3",
    auth_env: "auth_token",
  });
  assert.equal(gatewayEnv.ANTHROPIC_AUTH_TOKEN, "secret");
  assert.equal(gatewayEnv.ANTHROPIC_API_KEY, undefined);

  const legacyEnv = buildClaudeEnv({
    base_url: "https://gateway.example",
    api_key: "secret",
    model: "MiniMax-M3",
    auth_env: "both",
  });
  assert.equal(legacyEnv.ANTHROPIC_AUTH_TOKEN, "secret");
  assert.equal(legacyEnv.ANTHROPIC_API_KEY, "secret");
});

test("workerCommitMessage follows scoped Chinese commit convention", () => {
  const cliRow = {
    run_id: "run-1",
    worker_id: "w02",
    task_title: "任务流 CLI 参数校验",
    ownership_list: ["bin/taskflow.js"],
  };
  assert.equal(commitTypeFor(cliRow), "feat");
  assert.equal(commitScopeFor(cliRow), "cli");
  assert.equal(workerCommitMessage(cliRow), "feat(cli): 实现 任务流 CLI 参数校验");

  const testRow = {
    run_id: "run-1",
    worker_id: "w03",
    task_title: "任务流行为",
    ownership_list: ["test/**", "README.md"],
  };
  assert.equal(commitTypeFor(testRow), "test");
  assert.equal(commitScopeFor(testRow), "test");
  assert.equal(workerCommitMessage(testRow), "test(test): 增加 任务流行为 测试和文档");

  assert.equal(workerCommitMessage({
    run_id: "run-1",
    worker_id: "w04",
    ownership_list: ["bin/taskflow.js"],
    matrix_entry: {
      commit: {
        message: "feat(cli): 增加任务文件读取错误提示",
      },
    },
  }), "feat(cli): 增加任务文件读取错误提示");

  assert.equal(workerCommitMessage({
    run_id: "run-1",
    worker_id: "merge",
    ownership_list: ["integration"],
  }), "chore(integrate): 整合 run-1 worker 分支");
});

test("dependencyReadiness gates waves", () => {
  const rows = [
    { worker_id: "w01", status: "done", depends_on_list: [] },
    { worker_id: "w02", status: "planned", depends_on_list: ["w01"] },
    { worker_id: "w03", status: "planned", depends_on_list: ["w02"] },
  ];
  assert.deepEqual(dependencyReadiness(rows[1], rows), { ready: true, blocked: false, detail: "" });
  assert.equal(dependencyReadiness(rows[2], rows).ready, false);
  assert.equal(dependencyReadiness({ worker_id: "w04", depends_on_list: ["missing"] }, rows).blocked, true);
});

test("waitAssessment recognizes wave, all-done, integration, and failures", () => {
  assert.deepEqual(waitAssessment({
    workers: [
      { worker_id: "w01", status: "done" },
      { worker_id: "w02", status: "planned" },
    ],
  }, "wave-done"), { done: true, ok: true, reason: "wave_done" });

  assert.equal(waitAssessment({
    workers: [
      { worker_id: "w01", status: "done" },
      { worker_id: "w02", status: "running" },
    ],
  }, "all-done").done, false);

  assert.deepEqual(waitAssessment({
    workers: [
      { worker_id: "w01", status: "done" },
      { worker_id: "w02", status: "done" },
    ],
  }, "all-done"), { done: true, ok: true, reason: "all_done" });

  assert.equal(waitAssessment({
    workers: [
      { worker_id: "w01", status: "done" },
      { worker_id: "merge", status: "done" },
    ],
  }, "integrated").reason, "integrated");

  const failed = waitAssessment({
    workers: [
      { worker_id: "w01", status: "failed" },
      { worker_id: "w02", status: "planned" },
    ],
  }, "all-done");
  assert.equal(failed.done, true);
  assert.equal(failed.ok, false);
});

test("parseSummary reads marker fields", () => {
  const summary = parseSummary(`
done
<<<ORCH_SUMMARY
status: done
summary: implemented
key_changes: api; tests
verify: npm test passed
risks: none
next_steps: integrate
>>>
`);
  assert.equal(summary.status, "done");
  assert.equal(summary.summary, "implemented");
  assert.equal(summary.next_steps, "integrate");
});

test("selectChangeName handles explicit, single, empty, and ambiguous changes", () => {
  assert.equal(selectChangeName({ explicit: "chosen" }), "chosen");
  assert.equal(selectChangeName({ listData: { changes: [{ name: "only" }] } }), "only");
  assert.throws(() => selectChangeName({ listData: { changes: [] } }), CmdError);
  assert.throws(() => selectChangeName({ listData: { changes: [{ name: "a" }, { name: "b" }] } }), CmdError);
});

test("usage advertises OpenSpec parallel commands", () => {
  const text = usage();
  assert.match(text, /draft --goal <goal> --change <change> \[--workers n\]/);
  assert.match(text, /wait --run id/);
  assert.match(text, /integrate --run id/);
});
