本地部署基于 [Opik 官方 2.2.151](https://github.com/comet-ml/opik/tree/2.2.151)，
对应提交 `d5f5a04fe5e7b3eba0ed02092e1bdbfc0c6e7fa7`。

`deployment/` 下的官方 Compose、Nginx、ClickHouse 配置和说明保持原样。
本项目新增的覆盖文件为 `deployment/compose.local.yaml` 和
`deployment/clickhouse-local.xml`，启动入口为 `opik.sh`。

本地覆盖使用官方 `clickhouse/clickhouse-server:26.3.62.3` 标准镜像，
与上游 Alpine 镜像保持同一个 ClickHouse 版本，复用本机已有缓存。

首次部署遇到镜像内容下载缓慢，应用镜像通过
[南京大学公开 GHCR 缓存](https://doc.nju.edu.cn/books/e1654/page/ghcr) 下载。
其 ARM64 镜像清单与 GHCR 官方逐一核对相同，并逐层校验 SHA256 后导入原始镜像标签：

| 官方镜像（版本均为 2.2.151） | ARM64 清单 SHA256 |
| --- | --- |
| opik-backend | `8e695b4efd0f14cb21ebce2fb58a82fbdc024c9a78eb3f2a79d8f41f5eb555eb` |
| opik-python-backend | `d7d1bdef51e52fa6e359f773452084873b10c870eef7c1473c905ba7472f80b6` |
| opik-frontend | `a5b2d47be9d37edd0e074d4302870b6db72fa68990ec2d575e2be915b492d619` |

MySQL、Redis、ZooKeeper 从 [Docker 官方的 ECR 分发通道](https://www.docker.com/blog/news-from-aws-reinvent-docker-official-images-on-amazon-ecr-public/)
下载，同样核对 ARM64 清单与 Docker Hub 一致后导入原标签。
项目的 Compose 仓库地址保持上游原值，不修改 Docker 全局网络配置。

官方仓库许可证见 `UPSTREAM-LICENSE`。
