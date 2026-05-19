# 正式文档包使用说明

## 1. 你现在要怎么用

将本文件夹中的：

- `CLAUDE.md`
- `docs/` 目录下的全部文档

复制到你的项目仓库根目录中。

如果你已经把之前下载的“Claude Code 协同开发总方案”放到：

```text
docs/00_claude_code_collaboration_plan.md
```

请保留它，不要覆盖。

---

## 2. 复制后的仓库应类似

```text
your-repo/
├── CLAUDE.md
├── docs/
│   ├── 00_claude_code_collaboration_plan.md
│   ├── 01_prd.md
│   ├── 02_architecture.md
│   ├── ...
│   └── 17_phase0_claude_code_kickoff.md
```

---

## 3. 下一步

打开 Claude Code，进入仓库根目录，复制：

```text
docs/17_phase0_claude_code_kickoff.md
```

中的 Phase 0 指令给它。

注意：

- 先让 Claude Code 出 Plan；
- 不要让它立即动代码；
- 把 Plan 发回 ChatGPT 做审查；
- 审查通过后再让 Claude Code 执行。
