# Agent 驱动的仓库：方法论资产优先

> 从 SKILL.md 拆出（2026-09-04 拆薄）。2026-08-13 deepseek-harness 实战沉淀。

现代 agent 驱动开发的仓库（尤其 AI 公司开源项目），**`AGENTS.md` / `CLAUDE.md` / `.agents/` 目录本身就是金矿**——比源码更能代表「这个团队怎么用 agent 干活」。调查这类仓库时先扫这三处，别一头扎进 packages/。

- `.agents/skills/` — 团队实战 skill（code-review / trim-cot-leakage / find-simplifications / pre-push-checks…），每个都是可吸收的方法论
- `.agents/notes/` — 完整决策记录库：`{lifecycle}/{class}/yyyy-mm-dd-topic.md` 四态（proposed/implemented/rejected/archived 冻结）+ 六分类（feature/bug-fix/simplification/architecture/process/testing），每份必带 `## Alternatives considered`（「不记录它击败了什么，就是在邀请反复争论」）
- 仓库根 `AGENTS.md` / `CLAUDE.md` — 团队自己的 agent 工作流/约定

**提取流程**（本次实例）：
1. 列 tree 找 `.agents/`、`docs/` → 拉关键文档（README/architecture/user guide）确认产品是什么
2. **归档全量**：整 `.agents/` + docs + AGENTS.md 归档 `workspace/records/{repo}-reference/`（git 跟踪），不挑拣（git clone TLS 失败 → `curl -sL https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}` 直连 tarball 兜底，或 info-hunt 的 api.github.com/tarball）
3. **挑最对口的吸收**：评估每个 skill 与已有体系的对应关系，只吸收真正锋利且补缺口的点；patch 进已有 skill 并**标注出处**（`workspace/records/dsh-reference/...`），不拆新 skill；评估后不吸收的写明理由（太重/依赖特定环境/用不上）
4. 吸收点示范：决策记录必记「否掉的方案」（knowledge-persistence）；文档「会话视角泄漏」HEAD 测试（voice-rules 毛病 #6）；review 发现必带证据 + 推送前最小检查（github-ops）
