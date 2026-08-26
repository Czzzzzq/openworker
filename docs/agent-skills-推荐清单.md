# OpenWorker 加 Skill 推荐清单

> 基于仓库现状（skill 机制、连接器、依赖、已有工作流）与 [anthropics/skills](https://github.com/anthropics/skills) 官方生态调研，2026-08-24。

## 0. 你项目里 skill 是怎么工作的（30 秒回顾）

- 格式：一个文件夹 + `SKILL.md`，frontmatter 含 `name` / `description` / 可选 `allowed-tools`，正文为指令，可带 `resources/` 脚本。解析器见 `coworker/skills/base.py`，与 Anthropic 官方格式完全兼容。
- 存放位置：**`.dev-state/skills/`**（本机全局，不进 git）或 **`<workspace>/.coworker/skills/`**（随项目走 git，便于分享）。你现有的 `monday-status-report` 在 `.dev-state/skills/`。
- 加载方式：渐进式——会话开始只注入目录（name + description），agent 调用 `load_skill` 时才加载全文。
- 应用内还有 `save_skill` 工具（代码注释里叫 "worker-authors door"），agent 可以直接把做好的 skill 提案安装进来。

**结论：官方 anthropics/skills 仓库的 skill 可以直接复制目录进来用，无需改代码。**

---

## 1. 推荐总览（按性价比排序）

| 优先级 | Skill | 做什么 | 依赖 | 说明 |
|---|---|---|---|---|
| ⭐⭐⭐ | **docx / xlsx / pptx / pdf** | 产出/编辑 Word、Excel、PPT、PDF | `python-docx` `openpyxl` `python-pptx` `reportlab`（pdf 的 `pypdf` 仓库已有） | 这正是 OpenWorker"交付 finished work"的核心能力。官方文档类 skill 是 Claude 文档功能的同款实现，质量最高，**最值得装** |
| ⭐⭐⭐ | **skill-creator** | 教你从零写 skill、优化 description、跑 eval 评测 | 无 | 你要"加功能"最直接的入口，和 `save_skill` 流程天然配合 |
| ⭐⭐⭐ | **mcp-builder** | 从零构建 MCP server，给 agent 接外部 API/服务 | 无（生成 Python/TS 代码） | OpenWorker 原生支持 MCP，这是最灵活的"加功能"方式 |
| ⭐⭐ | **webapp-testing** | Playwright 测试本地 Web 应用（截图/日志/UI 调试） | `playwright`（仓库已有 optional dep） | 直接可用于测 openworker 的 GUI |
| ⭐⭐ | **doc-coauthoring / internal-comms** | 结构化协作写文档、公司内部沟通（状态/周报/FAQ/事故报告） | 无 | 你已写 `monday-status-report`，这两个能补全"写文档"场景 |
| ⭐⭐ | **theme-factory / web-artifacts-builder / frontend-design** | 带主题的产物美化（slides/文档/HTML 落地页）、复杂 React 产物、前端视觉设计 | 无 | 交付物颜值提升，GUI 开发也能用 |
| ⭐ | **canvas-design / algorithmic-art / slack-gif-creator / brand-guidelines** | 海报/生成艺术/GIF/品牌规范 | 各 skill 自带脚本 | 创意向，按兴趣选 |
| ⭐ | **claude-api / academy-guide / discernment-nudge** | Claude API 用法、教学引导、认知偏差提醒 | 无 | 与你场景关系不大 |

> 许可证提示：官方仓库示例 skill 多为 Apache-2.0；**docx/pptx/xlsx/pdf 四个是 source-available（非完全开源）**，自用没问题，别直接打包进你的商业发行版。

---

## 2. 官方文档类 skill 的依赖（装哪个补哪个）

```bash
# docx 需要
pip install python-docx
# xlsx 需要（官方还用了 pandas / markitdown，可选）
pip install openpyxl pandas markitdown
# pptx 需要
pip install python-pptx
# pdf：读取用 pypdf（已装），创建用 reportlab
pip install reportlab
```

安装说明：当前 `pyproject.toml` 没有这些，需要补进 `[project.optional-dependencies]`（建议加一个 `docs = [...]` extra，而不是核心依赖）。

---

## 3. 针对你仓库现状的定制 skill 建议（最有价值的增量）

这些是基于代码里实际看到的工作流设计的，官方仓库没有，需要现写（我照 `monday-status-report` 的模板写，5 分钟一个）：

### 3.1 费用审计 / 成本周报（强烈推荐）
仓库里已经有 `costmeter-ledger.csv`、`costmeter.jsonl`、`费用记录.md` 这套成本记账。写一个 skill：
- 汇总最近 7/30 天各 provider（DeepSeek、GLM、OpenAI…）的 token 与花费
- 对比上周、标出异常波动（某天/某模型突增）
- 生成/更新 `费用记录.md` 的中文周报小节
- 可以挂 automation 每周一自动跑，和 `monday-status-report` 合并成一份"周报全家桶"

### 3.2 中文 changelog / 发布说明
- `git log` 两个 tag 之间（或自上次发布以来）的 commit → 按主题归类的简体中文更新日志 markdown
- 复用你 `monday-status-report` 里"GitHub 活动 → 要点化"的模式，但输出给用户看的发布说明
- 直接支撑你仓库的版本发布

### 3.3 本地化同步（zh-CN 维护）
仓库有 `openworker-zh-CN/apply_zh_CN.py` + `zh-CN.json`。写一个 skill 指导：
- 如何把新增英文文案提取/补进 `zh-CN.json`
- 如何检查漏译、术语一致性（比如 cost meter、floating-icon 相关的新词）
- 跑 `apply_zh_CN.py` 验证

### 3.4 PR 代码审查 / 合并摘要
- 用 GitHub 连接器拉 PR 的 commits + diff + 讨论
- 按"改动意图 / 风险点 / 建议"输出中文审查要点（或合并摘要）
- 和你平时维护 openworker 上游同步的工作流契合

### 3.5 收件箱梳理 / 邮件周报
Gmail 连接器已就绪，但还缺"规则"层。写一个 skill 定义你的过滤/分类/摘要规则，支持"处理未读邮件"和"每周邮件摘要"两种模式。

---

## 4. 落地步骤

1. **官方 skill**：`git clone https://github.com/anthropics/skills`（走本地代理）→ 把 `skills/<名字>/` 整个目录复制到 `.coworker/skills/<名字>/`（想随仓库分享）或 `.dev-state/skills/<名字>/`（只本机用）。
2. **补依赖**：按第 2 节 `pip install` 要用的库，并把依赖写进 `pyproject.toml` 的 extras。
3. **自定义 skill**：照 `monday-status-report` 的模板写 `SKILL.md`，或先装 `skill-creator` 让它指导流程。
4. **验证**：新开一个会话，看 skill 是否出现在目录里；直接说"用 xxx skill 做……"测试触发。
5. **注意事项**：
   - `description` 写清楚**何时触发**（官方格式要求），否则 agent 不知道什么时候该加载；
   - 长指令放正文、别放 description，保持目录只有一行摘要（渐进式加载）；
   - 用到 shell/写文件等风险操作的 skill，可以在 frontmatter 里用 `allowed-tools` 收敛工具范围。

---

## 5. 我的建议：先装这 4 个，立刻见效

1. `skill-creator`（无依赖，0 成本，以后造 skill 都靠它）
2. `pdf` + `docx`（补 `pypdf`/`reportlab`/`python-docx`，覆盖日常文档交付）
3. 定制 skill：**费用审计**（你的 cost meter 数据已经在积累，不利用可惜）

需要的话我可以直接帮你：把官方 skill 装进仓库并补依赖、写 2~3 个定制 skill、给费用审计挂上周一自动执行。告诉我选哪些即可。
