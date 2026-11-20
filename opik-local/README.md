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
