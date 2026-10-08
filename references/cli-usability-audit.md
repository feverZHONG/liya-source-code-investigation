# 第三方工具可用程度静态评估（不 clone、不跑）

> 触发：有人甩一个 GitHub 项目/README 问「这工具能用吗」「CLI 可用程度如何」「值不值得用」。
> 沉淀自 2026-09-12 omniget（tonhowtf/omniget，Rust+Tauri 下载器，10k stars）CLI 评估。

## 铁律

**只读路径就能给硬结论。** 不 clone、不下载二进制、不执行。

**T 分级边界（答题时先用这条定住自己）：**「读源码给静态结论」＝**答事，可做**（群聊里也做）；「下二进制 / 装依赖 / 真跑一遗」＝**做事**，群聊挡回私聊（「这是做事不是答事，私聊找我」），私聊里也要先给静态结论、实测单独请示。

**回话时把这条边界说出来**——直接一句「要实测就私聊找我—群里不下二进制」，不解释、不展开。

## 抓取配方（全程不需要 git）

```bash
# ① 仓库元信息：default_branch/description/license/stars/created_at/pushed_at/topics
curl -s https://api.github.com/repos/{owner}/{repo}

# ② 全量文件树（一次拿 2000+ 条，存文件再用 python 正则过滤，别逐目录翻）
curl -s "https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1" -o /tmp/tree.json
# 过滤：re.compile(r'cli|main\.rs|Cargo\.toml|bin/|daemon|server|headless|\.md$', re.I)

# ③ 单文件直抓源码（blob 页 URL ≠ raw！README 给的是 html 页，raw 才是源码）
curl -sL "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"

# ④ release 产物矩阵
curl -s https://api.github.com/repos/{owner}/{repo}/releases/latest
```

- `topics` 字段直接暴露项目自我定位，比 description 诚实
- **有独立 CLI crate/目录**（如 `src-tauri/omniget-cli/`）就进去读它的 `Cargo.toml`/`package.json` + `src/main.rs`——入口文件通常几百字节，全读
- 有 agent 集成（`claude-plugin/skills/*/SKILL.md`、`INSTALL.md`）**一定读**：作者自己写的「依赖什么、失败怎么办」是最诚实的可用性文档
- ⚠️ `raw.githubusercontent` 偶发空响应（本次 reporter.rs 首次拿空、重试即成功）——**失败重试一次再下结论**，别当文件不存在
- 管道进 python 的写法会被安全扫描标记（可 auto-approve）：先 `-o /tmp/x.json` 再本地读，更干净

## 六个评估维度（下「可用程度」结论前逐条过）

| # | 维度 | 怎么查 | omniget 实测结论 |
|:--|:-----|:-------|:-----------------|
| 1 | **命令面** | 读 CLI 入口的子命令枚举 | 只有 4 条：info/download/batch/import-cookies |
| 2 | **能力差集** | GUI/主体能力 vs CLI 接线的差集 | core 有一长串 `set_xxx_fn`（限速/SponsorBlock/章节切分/元数据）全是 GUI 在用，CLI 一条没接 |
| 3 | **依赖链** | 是否外挂二进制；CLI 侧有没有自动安装链路 | 依赖 yt-dlp/ffmpeg；GUI 会自己下，CLI 只 `find_tool` 找现成的（PROD→app-data→系统） |
| 4 | **脚本友好度** | `--json` 有没有，**输出是不是合法 JSON** | 有 `--json`，但 `download.rs` 用 `{:?}` 打 Option，吐 `{"downloaded_bytes":Some(123)}`——非法 JSON |
| 5 | **参数面差集** | 拿上游工具（yt-dlp）当基线比 | 无文件名模板/无 cookies-from-browser/无自动字幕（`--subs` 只 write-subs 不开 auto-subs） |
| 6 | **版本资历 + 产物矩阵** | CLI 版本 vs 主程序版本；release 资产平台 | CLI 0.1.0 vs 主程序 0.9.2；Linux 只有 x86_64，无 aarch64 |

**红旗：同一个仓库里两份同类输出实现。** download.rs 手搓 `{:?}`、reporter.rs 有正确的 `serde_json::json!` 版本——**去读真正被调用的那一份**，别看到好实现就当真。

## 输出写法

「有/没有 → 命令面 → 卡点（按『会不会让脚本崩』排序）→ 评级」，最后给够/不够的对象（替上游工具不够 / 替 GUI 不够）。**不给装载教程**——人家问可用程度，不是问怎么装。
