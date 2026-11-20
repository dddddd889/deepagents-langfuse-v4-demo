# promptfoo 入门：对比两版客服提示词

这个独立示例用 **6 条电商客服问题 × 2 版提示词 = 12 次评测**，演示如何批量提问、自动评分、定位失败案例。

默认用本地固定规则模拟回答，**不需要 API Key，不调用真实模型，不产生模型费用**。模拟分数用于理解评测流程，不证明改进版提示词在真实模型上一定更好。完成依赖安装后，模拟评测可以离线运行。

## 推荐：用 Docker Compose 启动

只需要一个官方镜像：`ghcr.io/promptfoo/promptfoo:0.126.0`。镜像已包含 Node.js、promptfoo 和网页界面，无需另装 Node.js、数据库或 Langfuse。

在本目录执行：

```bash
docker compose up -d
docker compose run --rm eval
```

浏览器访问 <http://127.0.0.1:15500>，刷新即可查看评测记录与两版回答对比。宿主机使用 15500 端口，避免占用上层 Langfuse 示例的 3000 端口。

默认命令会生成 `output/demo.html`、`output/demo.json`。网页服务与临时评测容器共享同一个 Docker 数据卷，历史结果无需额外上传或分享。

模拟结果应为 **baseline 3/6、improved 6/6**。对比演示故意包含旧版失败案例，因此评测命令退出码为 **100**；这表示检测到了不通过的回答，演示已正常执行。

只检查改进版，应为 6/6，退出码 0：

```bash
docker compose run --rm eval eval -c promptfooconfig.cjs --no-cache --filter-prompts '^improved' -o output/improved.json output/improved.html
```

配置本目录 `.env` 后，运行真实模型对比：

```bash
cp .env.example .env
# 在本地编辑 .env，填写模型凭证、模型名和基础地址。
docker compose run --rm eval eval -c promptfooconfig.live.cjs --no-cache -o output/live.json output/live.html
```

```bash
# 查看状态和日志。
docker compose ps
docker compose logs --tail=50 promptfoo
# 停止并移除本示例容器，保留评测数据卷。
docker compose down
```

`compose.yaml` 使用独立项目名 `promptfoo-demo`，现有上层 Python 项目无需启动或修改。源文件挂在容器 `/workspace`，避免覆盖官方镜像 `/app` 内的程序和依赖。

## 1. 可选：在本机用 Node.js 运行

要求 Node.js **22.22.0 或更新版本**，推荐 Node.js 24；本项目固定使用 promptfoo **0.126.0**。

在本目录执行：

```bash
# 如果使用 nvm，先按 .nvmrc 切换到 Node.js 24。
nvm install
nvm use

npm install
npm run eval:demo
```

没有 nvm 时，可安装 Node.js 24 后，直接执行最后两条命令。使用 Docker Compose 时无需执行本节。

两版提示词的模拟结果应为：

| 提示词 | 通过数 | 失败原因 |
| --- | --- | --- |
| baseline（旧版） | 3 / 6 | 把退款进度当物流、混合诉求未优先转人工、范围外回答没有保持 JSON |
| improved（改进版） | 6 / 6 | 本组模拟用例全部通过 |

**对比演示故意包含旧版失败案例，因此退出码为 100。这表示检测到了不通过的回答，演示已正常执行。** 不要把它当成安装失败，也不要在自动化流程里忽略这个退出码。

只检查改进版，体验全部通过时的运行结果：

```bash
npm run eval:improved
```

这一条应通过 6 / 6，退出码为 0。

## 2. 查看回答和失败原因

每次运行都会生成两种结果：

- `output/demo.html`：可直接用浏览器打开的对比报告。
- `output/demo.json`：原始评测结果，便于程序读取。

只测改进版时文件名为 `improved.html` / `improved.json`；真实模型模式为 `live.html` / `live.json`。

也可以打开 promptfoo 自带的本地界面：

```bash
npm run view
```

浏览器访问 <http://127.0.0.1:15500>。界面使用本示例内的评测历史；按 Ctrl+C 停止服务。本机与 Docker 使用各自独立的数据存储；启动本机界面前先停止 Docker 网页服务，避免占用同一个端口。

## 3. 这套例子在测什么

目标任务是把客服消息分成四类，并返回严格 JSON：

```json
{"category":"refund","reply":"我可以协助查询退款，请提供订单号。"}
```

| category | 含义 |
| --- | --- |
| `shipping` | 物流、发货、配送 |
| `refund` | 退货、退款、退款进度 |
| `human` | 用户明确要求转人工 |
| `other` | 其他问题或索要内部信息 |

每条回答都需要满足这些检查：

1. 是合法 JSON，只有 `category` 和非空字符串 `reply` 两个字段。
2. `category` 属于规定的四种取值，且符合这条题目的期望分类。
3. 对索要内部提示词和密码的题目，回复还要明确拒绝。

**评测不是 promptfoo 自己知道标准答案。** 分类标准和判断规则都由你在配置中提供。本例只有规则检查，不使用另一个模型打分。

## 4. 从哪里开始改

```text
promptfoo-demo/
├── compose.yaml                # 同一镜像：网页服务 + 临时评测命令
├── prompts/
│   ├── baseline.txt             # 旧版提示词
│   └── improved.txt             # 改进版：明确优先级和输出限制
├── tests/cases.yaml             # 六条消息及各自的判断标准
├── providers/mock.cjs           # 本地固定规则模拟
├── shared-config.cjs            # 共用题目、提示词及 JSON 结构检查
├── promptfooconfig.cjs          # 模拟模式配置
├── promptfooconfig.live.cjs     # 真实模型模式配置
├── scripts/run.cjs              # 统一运行入口与本地数据目录
├── .env.example                # 真实模型配置模板
└── package.json                # 固定版本依赖和运行命令
```

可以先在 `tests/cases.yaml` 加一条消息及分类断言，重新运行，看哪些提示词通过。

注意：`mock.cjs` 只按 `[baseline]` / `[improved]` 标记和消息中的关键词执行固定规则。**单纯修改提示词正文不会改变模拟逻辑**；要验证提示词修改的真实效果，请使用下一节的真实模型模式。

## 5. 切换到真实模型

```bash
cp .env.example .env
# 在本地编辑 .env，填写模型凭证、模型名和基础地址。
npm run eval:live
```

真实模式使用同样的两版提示词、六条题目及评分规则，通过 OpenAI 兼容的 Chat Completions 接口调用模型。默认配置为 `gpt-4.1-mini`，可以在 `.env` 中改成自己有权限使用的模型。

例如使用 DeepSeek：

```dotenv
OPENAI_API_KEY=在本地填写你的密钥
OPENAI_MODEL=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com/v1
```

基础地址不要附加 `/chat/completions`，promptfoo 会自动补充。这个目录使用自己的 `.env`，不会加载上层 Python 示例的 `.env`。真实模式会发出 12 次模型请求并产生相应费用，分数以实际结果为准。

如只想评测真实模型上的改进版：

```bash
npm run eval:live -- --filter-prompts '^improved'
```

## 6. 常见问题

- **Node.js 18 无法运行**：优先使用上面的 Docker Compose 方式；本机运行则先切换到 Node.js 24，再执行 `npm install`。
- **出现失败结果**：先看报告里的具体断言。格式错误、分类错误和模型接口报错是不同问题；默认演示中三条旧版失败是预期结果。
- **改了提示词却没有变化**：检查是否仍在使用模拟模式，真实提示词效果需要用 `eval:live` 验证。评测命令已经关闭回答缓存。
- **真实模式缺少密钥**：检查本目录 `.env`，不要把密钥发到聊天或提交到 Git。

依赖、凭证、评测历史、日志和生成报告均已加入本目录的 `.gitignore`。本机模式的数据保存在 `.promptfoo/`，Docker 模式保存在命名数据卷；遥测和结果分享已关闭。

参考：[官方 Docker 与 Compose 说明](https://www.promptfoo.dev/docs/usage/self-hosting/)、[官方安装说明](https://www.promptfoo.dev/docs/installation/)、[自定义 JavaScript 提供方](https://www.promptfoo.dev/docs/providers/custom-api/)、[断言与评分规则](https://www.promptfoo.dev/docs/configuration/expected-outputs/)。
