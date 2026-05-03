# sqing-skills

个人 Codex / Claude Code Skills 仓库，用来集中保存、维护和分发常用的本地工作流能力。

这个仓库里的每个一级目录都是一个独立 Skill。Skill 通常包含 `SKILL.md`、可选的 `agents/openai.yaml`、脚本、示例配置和运行所需资源。使用时可以按需复制到项目的 `.codex/skills/`、`.claude/skills/`，或安装到对应工具支持的全局 Skills 目录。

## Skills

| Skill 名称                     | 语言/运行时 | 作用                                                                                                                                  |
| ------------------------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `claudecode-model-evaluator`   | Go          | 对同一个编码任务运行多个 Claude Code 模型后端，收集产物、测试结果和 diff，并生成中文横向评估报告。                                   |
| `git-finalizer`                | Node.js     | 在强模型或主 agent 写完代码后，使用高速 Claude Code worker 运行测试、审查 git diff、生成中文提交信息，并在验证通过后安全提交。        |
| `git-worktree-flow`            | Bash        | 提供 Git worktree 流程：修改前创建 worktree，完成后输出审查摘要，明确询问是否合并，并生成规范提交信息。                               |
| `github-feature-analyzer`      | Python      | 分析公开 GitHub 仓库中一个或多个功能的实现机制，按 README 优先、机制优先和行级证据生成中文报告。                                      |
| `tmux-claude-parallel-workers` | Node.js     | 通用并行实现工作流：主控代理负责拆解和规划任务，通过 tmux 启动多个 Claude Code worker 到独立 branch/worktree 中执行，并支持结果整合。 |
| `tmux-claude-openspec-worker`  | Node.js     | OpenSpec 单工实现工作流：父代理管理 OpenSpec 变更和实现规范，再启动一个 Claude Code worker 按 OpenSpec 上下文完成实现。               |

## Usage

复制某个 Skill 到项目内：

```bash
mkdir -p .codex/skills
cp -R ./git-finalizer .codex/skills/
```

或复制到 Claude Code 项目目录：

```bash
mkdir -p .claude/skills
cp -R ./tmux-claude-parallel-workers .claude/skills/
```
