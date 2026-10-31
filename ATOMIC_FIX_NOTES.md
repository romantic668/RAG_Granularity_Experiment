# 原子提取修正及下一步

已定位的问题：在原始 API 提取结果中，`text` 有时重复完整的复合句，只有引用片段分开。程序随后确实验证了该复合句。不是 CSV 导出把原子事实变回了整句。

修正：输出使用明确的 `claim_text` 字段；提示提供虚构的天气属性和人物事件拆分示例，强调每条只包含一个断言，并保留否定、范围、条件及不确定性。提取单独设置 low reasoning effort，所有组的验证仍为 none，固定模型不变。规则不是简单按 and 切分。引用匹配仍不能证明语义正确，需要人工审核。

## v3b：第一轮小检查后的补充

已审核用户上传的3条真实提取结果：4、10、5条事实，共19条；没有重复文本或引用回退，所有引用出现位置和范围有效。仍发现：

- 护理机构“像医院，并有共用房间”合为一条事实。
- “may have ... and offer ...”的后一个谓词丢失may，可能性变为确定陈述。
- 天气后续句的地点/日期未在事实文本中补全；风速引用只取数值，未包含可用的winds关系词。

v3b新增虚构的workshop/studio例子，分别演示属性拆分和情态词作用域；要求补齐上下文、引用关系词。它们是train阶段的流程修正，未使用test表现。真实效果仍需第二轮检查。

## v4b：本地手动修改提示词（在 v3b 之上，直接改 `ragstudy/core.py`）

不要再运行 `apply_http_fix.py`：它里面是 v3b 补丁，`core.py` 已有本地修改，安装器会拒绝覆盖。

新增规则：并列动词拆成各自的事实；目的/方式/工具/时长短语跟随其所属动作；共享修饰词留在每个并列项上；“the main X are:”这类框架语在每条事实里保留且不得弱化；事实不得以未解析的 it/they/these 开头；每个引文必须是原句中一个连续、未改动的子串（禁止 `...`、方括号、改变首字母大小写）。新增例子用的是应用功能、水壶、通风口，与已测样本内容无关。

验证（提取诊断，无验证器，约 0.29 USD）：
- 未参与调提示词的 `data/atomic_check_heldout8.jsonl`（8条，v3b 2次、v4b 2次）：回退、事实数、情态词保留基本持平，**没有可靠改善**，也没有明显退步；差异在随机波动范围内。
- `hard8`（发现问题的样本，已用于调提示词，只能说明问题是否消失）：“取出并静置”未拆开 2/3→0/3；“good”在并列项上保留 1/3→3/3；`main` 框架保留 0/3→部分出现。
- 仍然存在：未解析的 “These …”、非 main 框架（如 advantages）被丢弃、偶发漏掉一个事实（14185 的 renewal term）、偶发拆出目的句、偶发引文大小写不匹配导致 sentence_fallback。提取有随机性，同一提示词多次结果不同。
- 结论：再继续改提示词收益很小，容易对这些样本过拟合。pilot 阶段用 `claim_audit.csv` 量化这些问题并写入局限性；正式实验对原子事实组重复运行（`--repeat`）并报告波动。

## 现在执行

重新下载最新版 `apply_atomic_fix.py`，覆盖上次同名安装脚本，然后放在 `experiment.py` 同一目录，在 PowerShell 中执行：

```powershell
py -3 apply_atomic_fix.py
py -3 atomic_tools.py check --out runs/atomic_check_v3b --max-requests 6 --max-usd 0.5
```

安装不调用付费 API。最后一条通常调用提取模型3次；重试计入6次上限，0.5 USD为软阈值。没有运行验证器，因此没有检测分数。

完成后上传 `runs/atomic_check_v3b/atomic_check.json`。它含原文、拆分事实及引用，可检查单事实性、意思保留、事实完整、原文映射。不要仅凭事实条数判断通过。样本15798、16825、13958来自旧 train pilot 中的结构问题，用于诊断，不用于估计检测性能。

## 检查通过后的 pilot

```powershell
py -3 atomic_tools.py reuse --source runs/pilot_v2 --out runs/pilot_v3
py -3 experiment.py run --input data/pilot60.jsonl --out runs/pilot_v3 --phase pilot --max-requests 6000 --max-usd 5
py -3 experiment.py evaluate --input data/pilot60.jsonl --run-dir runs/pilot_v3 --bootstrap 1000
py -3 experiment.py estimate --run-dir runs/pilot_v3 --n 300
```

旧目录名字不同时改 `--source`。`reuse` 不调用 API，只将数据、完整请求、模型及原始结果均匹配的 response/sentence 缓存复制到新目录。已上传的 pilot_v2 中501个调用可复用；旧 atomic 全部不导入。保留历史耗时和方法费用，报告会披露复用来源；新目录预算只累计新请求。启动 run 后断点续跑只重执行 run，不再执行 reuse。不能复用旧版冻结协议。

pilot_v3 完成后按原手册审核 claim_audit.csv、response_audit.csv 和错误案例，将实际结论填入 templates/pilot_audit_notes.md。小诊断通过不等于60条提取全部正确。

## 审核后再正式300条

```powershell
py -3 experiment.py freeze --pilot-dir runs/pilot_v3 --final-data data/final300.jsonl --out protocol_v3.json --audit-notes templates/pilot_audit_notes.md
py -3 experiment.py run --input data/final300.jsonl --out runs/final_v3_r1 --phase final --lock protocol_v3.json --max-requests 20000 --max-usd 20 --repeat 1
py -3 experiment.py evaluate --input data/final300.jsonl --run-dir runs/final_v3_r1 --bootstrap 1000
```

5/20 USD均为示例软阈值，按修正版 pilot 的估算和实际账单决定预算。正式实验全部三组重新调用；不使用旧 pilot 缓存。不得根据 test 分数调整提取提示或选择有利样本。

## 本地验证范围

通过30项项目测试，覆盖 claim_text 实际进入验证器、阶段参数、无标签泄漏、只提取诊断、缓存完整请求匹配、错误拒绝、历史资源保留、续跑、传输与冻结。另通过4项安装检查，覆盖安装备份、重复安装、回滚、自定义修改保护及v3到v3b升级。已对用户上传文件核对501个基线调用均可匹配。此环境未调用真实 OpenAI 模型，修正版的实际语义质量仍待小诊断审核。
