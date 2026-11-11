# Libri 课程项目 · B 部分进展与交接

2026-11-10 · Yongqi (Ashley) Ou · Python 规则 v0.11 · 本地接口草案 `libri-b-candidate-0.11-local`

Libri 想帮助开发者查技术文档时看清一项操作的前提、默认行为和例外，并能回到原文核对。Ashley 的 B 部分负责把英文段落转换为带来源的结构化候选。Benny 的 OCaml 模块可以用这些候选保存、重载和查询。Avery 的评价模块可以比较各方法保留了多少原文含义，以及哪些限定被漏掉或绑到了错误的动作上。

**当前里程碑。** B 的抽取程序已能在本地运行。32 个真实文档目标的预测、逐项开发复核和四种方法的比较均已完成。6 个开发例的人工校准已完成 3 个，实际下游读取和课程报告仍在推进。团队接口和正式评价仍待对齐。

## 已完成的工作

| 工作 | 当前产物 |
|---|---|
| 数据与来源 | 从 Git 的 `git add`、`git rm` 和 Docker 的重启策略、live restore 四篇文档选出 32 个开发目标，Git/Docker 各 16 个。每个目标保留输入段落、目标句位置、文档修订和章节。对照原文整理了 97 项需要保住的含义。 |
| NLP 抽取 | 使用 Stanza 的英文分词、词形和依存分析提取动作与参与者。规则能在部分句式中绑定否定、默认、模态、条件与介词短语。每条预测都可回查原文，解析不全时保留已识别结构、未决线索和原因。 |
| 实验 | 在相同目标、原文和默认句法解析上比较旧规则、当前规则、只取主谓宾中心词的基线和移除指定限定结构的消融版本。空结果计入分母。输入、解析和预测均已冻结，可离线重放。 |

| 开发集上的方法 | 含义完整保留（共 97 项） | 该目标所有要求均完整（共 32 个目标） |
|---|---:|---:|
| 旧规则 v0.5 | 21 | 4 |
| 当前规则 v0.11，默认文本输入 | 33 | 5 |
| 简单主谓宾（SVO） | 1 | 0 |
| 当前规则移除指定限定结构 | 21 | 2 |

当前默认规则的 97 项中，33 项完整、23 项部分结构化、35 项仅保留局部文字、6 项缺失。新版比旧版多完整保留了 12 项，但只多了 1 条整目标完整。一个句子即使抽对了主动作，也可能漏掉版本例外或漏判一个条件到底限制哪个动作。以上是 AI 助手按原文逐项作出的开发判断。对未见文档的准确率及人工标注一致性仍待测量。

## 用一条实际输出对齐 A、B、C

Docker 文档在同一句中说明 live restore 可让容器跨 daemon 更新继续运行，随后把支持范围限定为补丁版本，排除主版本升级。B 在输出里找到了这整段限制，尚未把它解析成一条有明确对象和范围的支持关系。[Docker 固定版本原文，第 53–55 行](https://github.com/docker/docs/blob/dc98e192e7879ab36c55905844feda9685487324/content/manuals/engine/daemon/live-restore.md#L53-L55)

该例的完整 `input.text` 如下，可用它直接核对字符位置。

> Live restore allows you to keep containers running across Docker daemon updates, but is only supported when installing patch releases (YY.MM.x), not for major (YY.MM) daemon upgrades.

在真实记录 `nr-docker-patch-only-11` 中，`target_span` 为 `[0,183]`，`candidate.status` 为 `partial`，`candidate.candidate_structure` 为 `null`。下面是程序输出的完整 `candidate.qualifiers[2]` 对象。它作为一个限定存放在记录内，整条记录还另有输入、来源、动作和其他限定。

```json
{
  "id": "q3",
  "kind": "unmodeled",
  "cue": {
    "text": ", but is only supported when installing patch releases (YY.MM.x), not for major (YY.MM) daemon upgrades",
    "spans": [[79, 182]],
    "word_ids": [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37]
  },
  "target": "f1",
  "interpretation_status": "unresolved"
}
```

| 谁读取 | 怎样使用这条记录 |
|---|---|
| Benny / A | 主结果在 `records[].candidate`。`target="f1"` 保留 q3 与候选动作的关联，`unresolved` 说明具体版本关系尚待解释。建议的联调检查是保存后重载，核对 q3 原文、范围、未决状态和来源能否一起恢复。 |
| Avery / C | 该目标有 4 项开发要求，当前为 1 项 `partial`、3 项 `text_only`。例如 r4「排除 major 升级」能在 q3 中定位，所以计作 `text_only`。要计为 `retained`，输出还须表达排除的是哪项功能的支持范围。仅回到输入全文才能找回的要求按 `missing` 处理。 |

**输入和位置约定。** `input.text` 是送入解析器的清洗后段落，`target_span` 指定本条评价的范围。`cue.spans` 与 `target_span` 均以该段落的 Unicode 码点计数，从 0 开始，右端不包含在内。`input.source` 保存文档 URL、修订、章节及原文件行号，可用于回查。OCaml 端若使用 UTF-8 字节索引，需要先换算。

**另外两种结果。** 在 `docker-restart-prerequisite-001` 中，规则绑定了“容器成功启动”是“重启策略生效”的必要前提，方向为 `main_requires_condition`。原段落下一句给出的“至少运行 10 秒且 Docker 已开始监控”保留在上下文中，跨句定义仍待绑定。在 `docker-policy-only-containers-003` 中，程序运行成功，但输出为 `unsupported` 且动作列表为空。下游仍可显示原句并把该例计入评价。程序的 `pattern_covered`、`partial`、`ambiguous`、`unsupported` 表示规则处理状态，含义是否完整则按每项来源要求另评。[Docker 固定版本原文，第 61–72 行](https://github.com/docker/docs/blob/dc98e192e7879ab36c55905844feda9685487324/content/manuals/engine/containers/start-containers-automatically.md#L61-L72)

## 剩余工作及对接点

| 事项 | 下一步与完成标志 |
|---|---|
| Ashley 可以独立推进 | Ashley 已完成 3/6 个开发目标的人工校准，继续记录其余三例的判断和原文依据。再验证未决、歧义和空结果的离线读取，把方法、结果、错误实例和限制写入组内报告，并整理可重跑的最终 B 版本。 |
| Benny 与 Ashley 对接 | 一条 OCaml 实际期望的输入记录或对应类型即可开始适配。若已有导入器，可直接拿上例核对保存和重载后保留的字段。以实际读写结果确认接口。 |
| Avery 与 Ashley 对接 | 用上例的 r4 对齐一条评分记录，确认文字保留、正确绑定和无输出分别怎样计分。正式评价另取未见文档组做独立双标。可讨论对固定单位上的类别使用 Cohen’s κ，对位置及绑定另记匹配结果。 |
| LLM 对照 | 已为指定模型 `gpt-5.4-mini-2026-03-17` 准备开发集输入、提示和运行配置。计划先做小批调用，再按实际得到的有效预测和失败记录报告结果。当前尚无有效预测。 |

这份快照对应 2026-11-10 的 B 本地进展。A 的实际导入与 C 的正式评分可在拿到对应样例后分别验证。
