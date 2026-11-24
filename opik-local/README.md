# Opik 本地部署

使用官方 **Opik 2.2.151** 镜像，通过 Docker Compose 启动独立的本地实例。

- 页面：<http://127.0.0.1:5174>
- SDK / API 地址：`http://127.0.0.1:5174/api`
- Compose 项目名：`opik-local`
- 数据保存在独立的 Docker 命名卷中，停止服务后仍保留。

## 启动和日常操作

需要 Docker 正在运行，以及 Docker Compose **2.24.4 或更新版本**，以支持本地覆盖配置使用的 `!override`。应用镜像支持 Apple Silicon 原生运行。

```bash
cd /Users/yhding/ws/demo/deepagents-langfuse-v4-demo/opik-local

# 下载固定版本镜像并启动，不从源码构建。
./opik.sh start

# 查看容器状态、日志。
./opik.sh status
./opik.sh logs

# 停止 Opik，保留数据。
./opik.sh stop

# 再次启动。
./opik.sh start
```

脚本根据自身所在目录定位配置，也可以从任意目录通过绝对路径调用：

```bash
/Users/yhding/ws/demo/deepagents-langfuse-v4-demo/opik-local/opik.sh status
```

如需提前下载镜像，运行 `./opik.sh pull`。启动使用已发布镜像和 `--no-build`，不会自动升级到开发版。默认不启动 Guardrails 和 OpenTelemetry 附加服务。

## 验证服务和数据写入

```bash
# 检查页面服务与后端 API。
curl --fail http://127.0.0.1:5174/health
curl --fail http://127.0.0.1:5174/api/is-alive/ping

# 创建或使用 opik-local-demo 项目，写入一条验证 trace 并读回。
./opik.sh smoke
```

`smoke.py` 只使用 Python 标准库，不调用模型，也不需要模型密钥。完成后可以在页面中的 `opik-local-demo` 项目查看验证记录。

2026-11-20 本机部署已验证：8 个常驻服务健康，页面健康接口和后端 API 正常，
`smoke` 成功写入并读回输入与输出。验证记录编号为
`01a1f818-6789-7646-9025-d35877bd8437`。

## 一步步运行订单 Agent 和 session 评估

这个样例从原 Langfuse 项目的 `demo.py` 适配而来，沿用 DeepAgents、
`query_order` 工具和 `ORD-1001` 的固定业务事实。模型回答、工具调用和整段会话评分均真实执行。
原 Langfuse 入口保持可用，Opik 样例使用本目录独立的 `.venv` 和 `uv.lock`。

2026-11-23 已实际跑通：两个独立会话各 3 轮、共 6 条问答 trace，
工具 span 均在同一项目下，三项原生反馈分数均已回写；仅重评已有会话也已验证。
本机可直接打开 [已生成的 Threads](http://127.0.0.1:5174/default/projects/01a206ef-377f-714f-aa5a-99f21b13d3a5/logs?logsType=threads&time_range=past30days)，
查找 `order-known-8758e7494b` 和 `order-missing-8758e7494b`。本次实际分数均为 1，理由可在记录中查看。

### 1. 确认准备条件

需要 Python 3.12、`uv` 和正在运行的 Opik。AI Providers 中已有 `TAMS` 配置，
并登记 `bailian/qwen3.8-flash`。本脚本通过 Opik 网关复用保存的凭据，
不需要把 `TAMS_TOKEN` 再写进 `.env`。

```bash
cd /Users/yhding/ws/demo/deepagents-langfuse-v4-demo/opik-local
./opik.sh status
```

8 个常驻服务应该显示 `healthy`；初始化任务 `Exited (0)` 是正常结果。
如果 Opik 尚未启动，先运行 `./opik.sh start`。本机资源有限时应先停止 Langfuse。

### 2. 一条命令跑完整流程

```bash
./opik.sh session-demo
```

首次会安装锁定的 Python 依赖。随后自动运行两个互相隔离的 session，每个 3 轮：

| 会话前缀 | 用户问题 | 要观察的行为 |
| --- | --- | --- |
| `order-known-` | 查询 ORD-1001 → 追问商品和承运商 → 总结订单事实 | 真实调用工具，后续轮次正确理解“刚才那单” |
| `order-missing-` | 查询 ORD-404 → 追问单号 → 总结和下一步 | 如实说明未找到，不编造或借用另一会话的单号 |

终端会逐轮打印问题、实际回答、工具结果、Trace ID，最后打印两个会话的评分和页面链接。
每次运行生成新 session ID；**这会真实调用模型并产生服务端计费**。
不会通过暂停或启动原 Langfuse 服务来执行这个命令。

同一 session 的 Agent 历史保存在进程内检查点，每轮追踪使用同一 `thread_id`。
不同 session 的检查点隔离；退出脚本后检查点不继续保存，Opik 中的追踪和评分仍保留。
这里验证的是单会话多轮上下文和多个 session 的独立评估，未增加跨 session 长期记忆。

### 3. 在页面查看整段对话

打开终端打印的链接，项目名为 **deepagents-opik-order-demo**，进入 **Logs → Threads**。
应该看到这次的 `order-known-...` 和 `order-missing-...` 两段会话。
点开一段会话，可以查看三轮用户问题与模型回答。

点击某轮的 Trace，再展开 `deepagents-order-agent` 和 `query_order`，
查看模型调用、工具入参、返回值和耗时。找到的订单应包含：
`已发货`、`顺丰`、`SF-DEMO-1001`；未找到的订单应返回 `found=false`。

### 4. 查看会话评分

脚本会创建本样例专用的原生 Threads 规则，关闭会话后主动提交评分，
等待结果回写；不需要等待自动评分的冷却时间。

| 分数字段 | 判断内容 |
| --- | --- |
| `order_accuracy` | 订单状态、物流等事实是否准确 |
| `context_retention` | 后续轮次是否保持并正确引用本会话上下文 |
| `task_completion` | 是否完成各轮要求，未找到订单时是否如实说明 |

分数是评分模型实际返回的布尔结果，在反馈分数中表现为 `1` / `0`，并带具体理由；
脚本不会预先填写满分。进入 **Online evaluation** 可以查看
`deepagents-order-session-...` 规则的提示词、TAMS 模型和日志。
规则默认暂停，脚本每次显式触发一次，便于控制评分次数。

### 5. 自己从页面触发一次评分

如果想亲手点选 Evaluate，先只生成会话：

```bash
./opik.sh session-demo --no-evaluate
```

然后在 **Logs → Threads** 勾选刚生成的会话，点击 **Evaluate**，
选择 `deepagents-order-session-...` 规则并提交。即使规则处于暂停状态，
手动评估仍可运行。稍等并刷新即可看到反馈分数。
该命令也会创建暂停的评分规则，首次运行即可直接从页面触发评分。

也可以只评已有会话，避免重跑 Agent；把示例编号替换为终端打印的真实编号：

```bash
./opik.sh session-demo --evaluate-thread order-known-你的编号
```

更换已在 Opik 登记的模型时：

```bash
./opik.sh session-demo --model custom-llm/TAMS/bailian/qwen3.8-flash
```

如果评分等待超时，先看对应规则的日志或刷新 Threads，不要反复运行完整流程。
`--evaluation-timeout 300` 可以延长等待时间。

实现入口：`order_demo.py` 负责真实多轮 Agent，`session_evaluation.py` 负责
原生规则和反馈分数回读，`smoke.py` 仍用于原有的单条记录连通性验证。

本地会话历史由 `InMemorySaver` 在当前脚本进程内保存；两个会话使用不同编号，
不会互相带入历史。脚本退出后，历史追踪和分数继续保存在 Opik，Agent 的检查点不会跨进程保留。
这是同一会话内的多轮记忆演示；跨会话长期记忆需要另接持久化记忆存储。

会话评分需要同时读取 trace 和工具 span。本机实测原来的 ClickHouse 内部 1.5GB 预算
会触发 `MEMORY_LIMIT_EXCEEDED`，现已调整为内部 2.5GiB、容器上限 3GiB。
Langfuse 保持停止时，可在当前约 7.75GiB 的 Docker 内存下运行本样例。

## 运行 PDF 主 Agent 和阅读子 Agent

```bash
cd /Users/yhding/ws/demo/deepagents-langfuse-v4-demo/opik-local
./opik.sh pdf-demo

# 也可以换成自己的 PDF。
./opik.sh pdf-demo --pdf /绝对路径/文件.pdf --question "概述主要结论，并逐项标注来源页码"
```

默认分析仓库已有的 `2507.21504v1.pdf`，即 11 页论文
《Evaluation and Benchmarking of LLM Agents: A Survey》。通过已配置的 TAMS 网关真实调用模型。
PDF 入口默认使用 `bailian/qwen3.8-max`，开启流式响应；可用 `--model` 更换已登记模型。
主 Agent 必须调用 `task` 委派 `pdf-reader-agent`；子 Agent 通过 `get_pdf_info` 和
`read_pdf_page` 读取各页文字。工具按实际字符区间记录覆盖情况，不依赖模型宣称已读完。
主、子 Agent 和覆盖检查复用仓库根目录 `pdf_agent.py`，逐页工具复用 `pdf_tools.py`。

终端打印工具进度和追踪链接。项目为 **deepagents-opik-pdf-demo**，
可以在同一条 trace 查看原 PDF 附件、主子 Agent、模型输入中的系统提示词、
每次逐页工具输入输出，以及最终分析。报告保存到 `output/opik-pdf/analysis-运行编号.md`，
每次使用新文件名，不覆盖先前结果。

此入口执行真实 PDF 分析和追踪，不自动评分，也不启动或停止任何 Docker 服务。
当前仅提取 PDF 文字层，不包含图表视觉理解或 OCR；空白文字页会单独列为需要 OCR。
阅读子 Agent 单次运行最多调用模型 20 次、逐页读取工具 50 次，超限会明确失败，避免重复读取持续计费。

2026-11-24 已实际跑通：1 次主子 Agent 委派、14 次逐页/分段读取、6 次模型调用，
11 页文字层全部覆盖；62 个子节点均在同一 Opik 项目，所有模型输入均包含系统提示词。
原 PDF 附件下载后 SHA256 与本地文件一致。可打开
[本次成功追踪](http://127.0.0.1:5174/default/projects/01a20b46-bbd3-7473-8baa-c994dc0e7e1c/logs?logsType=traces&time_range=past30days&trace=01a20b4d-5da9-719e-a95d-c9f48e44659d)。

排障记录：默认订单模型 `qwen3.8-flash` 首轮在全文读取后重复读页，收紧提示后又在
汇总阶段遇到网关 HTTP 504；本次改用 `qwen3.8-max`、流式响应和每次最多 4000 个输出 token 后成功。
这些调整与成功运行同时发生，尚未单独验证是哪一项解决了超时。早期失败记录保留在项目中。

## 运行上下文压缩、HTTP skill、MCP 和完整评估

```bash
# 在仓库根目录执行；默认使用同一份 11 页 PDF。
./opik-local/opik.sh evidence-demo

# 仅生成证据，之后单独运行取证评分。
./opik-local/opik.sh evidence-demo --no-evaluate

# 只重评已有记录，不重新运行 PDF Agent。
./opik-local/opik.sh evidence-demo --evaluate-trace 终端打印的Trace编号
```

项目为 **deepagents-opik-pdf-evidence-demo**。这条入口按顺序实际执行：

1. 临时启动只监听 `127.0.0.1` 随机端口的 skill/MCP 服务。
2. `skill.load` 通过 HTTP 下载本目录 `skills/pdf-review/SKILL.md`，记录正文、版本、ETag、哈希与附件。
3. `mcp.initialize` 完成协议协商，`mcp.tools.list` 发现两个 PDF 工具并保存参数格式快照。
4. `context.compact` 压缩明确标注为 `demo_fixture` 的预置历史。旧历史超过 4000 字符时，
   调用模型生成摘要，以摘要和当前问题替换后续 Agent 的旧消息；原始历史、摘要与替换结果仍保留。
   这是运行入口控制的压缩演示，不代表某次真实用户历史或框架默认阈值已经触发。
5. 主 Agent 通过 `task` 委派子 Agent。子 Agent 的 PDF 读取工具通过标准 MCP `tools/call`
   经 HTTP 执行，客户端不直接调用 PDF 解析函数。工具 span 中记录 `tool_origin=mcp`、
   `mcp_method=tools/call`、服务与工具清单编号；结果 `_mcp.request_id` 是服务端收到的实际请求编号。
6. 每次模型调用保存有效 `messages` 和实际工具定义快照，原有 LLM span 补充
   `context_snapshot_id`、`tool_manifest_id`、skill 与 MCP 清单引用；无需再创建重复的模型 span。
7. 上传完成后，对本次成功 trace 运行只读取证评估 Agent，将实际分数和独立取证轨迹写回 Opik。

预置旧历史中含有报告开头标识，用来检验压缩是否保留约束；当前问题和 skill 都不会重新注入该标识。
原始证据保存于 `output/opik-evidence/运行编号/`，并作为对应 Opik 节点的附件上传。
业务分析报告仍保存在 `output/opik-pdf/`；完成后追加实际评分及评分调用链链接，
旁边保存 `.evaluation.json`。临时 HTTP/MCP 服务在 Agent 完成或失败后关闭。

| 分数字段 | 判断内容 |
| --- | --- |
| `verified_context_retention` | 压缩是否保留旧约束，摘要是否进入业务模型输入，最终是否遵守 |
| `verified_skill_following` | HTTP 正文是否进入实际系统提示，Agent 是否按规则完成委派及阅读 |
| `verified_mcp_execution` | 是否先发现工具，再用合法参数实际调用 MCP，并核对返回值 |
| `verified_pdf_grounding` | 抽查的主要结论是否有 PDF 页码与工具事实支持，是否如实说明文字提取的边界 |

以上分数由评估模型实际给出，不预设为 1。取证裁判只持有四个只读工具：
`read_compression_evidence`、`read_skill_evidence`、`read_mcp_evidence`、`read_pdf_evidence`，
从 Opik 回读业务记录及全部 span 分页，不执行原 PDF/MCP 工具。
必须完成四类取证、抽查至少两个不同 PDF 页码，并引用真实存在的业务 span 编号，评分才会写入。
单次裁判最多 8 次模型调用、12 次取证工具调用；未满足条件时明确失败，不写预设分数。
在业务 trace 的反馈分数查看 `verified_` 四项及理由，在 **pdf-evidence-evaluation** trace
查看裁判的模型输入、工具取证、抽查页码与结构化输出。

本机 Opik 2.2.151 的原生裁判将 `custom-llm/TAMS/bailian/qwen3.8-max` 识别为不支持工具：
实际规则日志显示 `tools=0, toolsEnabled=false`，长轨迹回退后只向模型传递根记录，
因此其最初四个满分不能视为完整取证结论。默认入口已改为上述项目内评估 Agent，
依然使用本机 Opik 保存轨迹和反馈分数；原生初评规则保持暂停，历史记录保留用于排障。
本次报告的 `.native-evaluation.json` 保留初评，`.evaluation.json` 保存经过取证门槛校验的最终结果。
遇到评分问题可以使用只重评命令；加 `--output 已有报告.md` 可把新结果附到报告中，避免重跑整套业务 Agent。

本入口固定使用官方 MCP Python SDK `1.30.0` 的 v1 接口与独立锁文件，不影响原 Langfuse 环境。
2026-11-24 已实际验证：业务轨迹 127 个 span，测试历史由 5968 压缩为 240 字符；
HTTP skill 下载 1 次、MCP 工具调用 15 次（14 次读页/分段读取和 1 次文档信息），11 页文字全部覆盖。
18 次业务模型调用均保存了实际上下文与工具定义，skill 正文都出现在系统提示中；
压缩前后、skill、MCP 清单及一个实际模型上下文共 5 份附件下载后校验哈希一致。

取证裁判实际调用了四类只读工具，并抽查第 1、6、8、11 页，四项 `verified_` 分数均为 1，
每个评分理由均引用了本次真实业务 span。可以直接打开：

- [完整业务轨迹和反馈分数](http://127.0.0.1:5174/default/projects/01a20b6d-d53f-7483-9856-b0199aef9b3c/logs?logsType=traces&time_range=past30days&trace=01a20b6e-046c-737f-b840-f4b71524e064)
- [裁判的模型调用与取证过程](http://127.0.0.1:5174/default/projects/01a20b6d-d53f-7483-9856-b0199aef9b3c/logs?logsType=traces&time_range=past30days&trace=01a20b78-b4fd-71aa-9325-4b1cc16dc6f4)

本地无模型测试：

```bash
opik-local/.venv/bin/python -m unittest discover -s opik-local/tests
```

## 接入本地应用

自托管实例不需要 Opik API Key。将应用的 SDK 地址指向本实例：

```bash
export OPIK_URL_OVERRIDE=http://127.0.0.1:5174/api
export OPIK_PROJECT_NAME=opik-local-demo
```

上面的地址适用于运行在本机的应用。容器中的 `127.0.0.1` 指向容器自身，不能直接用它连接宿主机的 Opik。

## 配置与来源

`.env.example` 提供配置模板，`.env` 保存本机配置。默认固定：

```dotenv
OPIK_VERSION=2.2.151
OPIK_PORT=5174
CREATE_DEMO_DATA=false
OPIK_USAGE_REPORT_ENABLED=false
```

默认只开放 `127.0.0.1:5174`，数据库和后端管理端口不向宿主机开放。修改端口后重新运行 `./opik.sh start`，并同步修改应用中的 `OPIK_URL_OVERRIDE`。

`deployment/` 中的官方部署文件来自标签 `2.2.151`，对应提交 `d5f5a04fe5e7b3eba0ed02092e1bdbfc0c6e7fa7`。本机端口和资源设置集中在 `deployment/compose.local.yaml`。统一通过 `opik.sh` 操作，脚本会明确选择官方配置和本地覆盖文件。

参考：[官方本地部署文档](https://www.comet.com/docs/opik/self-host/local_deployment)、[2.2.151 官方部署源码](https://github.com/comet-ml/opik/tree/2.2.151/deployment/docker-compose)。

## 恢复原有 Langfuse

本次部署前，Docker 分配约 7.75 GiB 内存，现有服务占用较多。经用户授权，原有 Langfuse 的 6 个 `deepagents-langfuse-demo-*` 容器已暂时停止；容器和数据卷均保留。

需要切回 Langfuse 时运行：

```bash
./opik.sh restore-langfuse
```

该命令先停止 Opik，再启动原有的 6 个 Langfuse 容器。恢复后可通过原来的地址 <http://127.0.0.1:3000> 访问。之后如需同时运行两套服务，应先给 Docker 分配足够内存。
