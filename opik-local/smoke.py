#!/usr/bin/env python3
"""用真实 HTTP 写入和读取验证本地 Opik，不安装 SDK，也不调用模型。"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import UUID


def read_local_config():
    """只读取项目自己的环境配置，不执行配置文件里的任何 shell 代码。"""
    values = {}
    config_file = Path(__file__).resolve().parent / ".env"
    if config_file.exists():
        for line in config_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values


def request_api(opener, base_url, method, path, payload=None):
    """发送本地 API 请求，保留响应头以取得后端生成的 trace 编号。"""
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Comet-Workspace": "default"},
    )
    with opener.open(request, timeout=30) as response:
        text = response.read().decode("utf-8")
        return (json.loads(text) if text else None), response.headers


def ensure_project(opener, base_url, project_name):
    """精确查找验证项目，不存在时创建；重复运行不会重复创建项目。"""
    try:
        project, _ = request_api(
            opener, base_url, "POST", "/v1/private/projects/retrieve", {"name": project_name}
        )
        return project
    except HTTPError as error:
        if error.code != 404:
            raise
    request_api(
        opener,
        base_url,
        "POST",
        "/v1/private/projects",
        {"name": project_name, "description": "Opik 本地部署验证，不调用模型"},
    )
    project, _ = request_api(
        opener, base_url, "POST", "/v1/private/projects/retrieve", {"name": project_name}
    )
    return project


def run_smoke(base_url, project_name, version):
    """创建一条可辨认的验证记录并读回，检查完整的数据库写入链路。"""
    # 本地验证直连服务，避免本机代理环境把 localhost 请求转发到外部。
    opener = build_opener(ProxyHandler({}))
    project = ensure_project(opener, base_url, project_name)
    started_at = datetime.now(timezone.utc)
    expected_input = {"check": "local-deployment"}
    expected_output = {"status": "ok"}
    _, headers = request_api(
        opener,
        base_url,
        "POST",
        "/v1/private/traces",
        {
            "project_name": project_name,
            "name": "local-deployment-smoke",
            "start_time": started_at.isoformat(),
            "end_time": (started_at + timedelta(milliseconds=1)).isoformat(),
            "input": expected_input,
            "output": expected_output,
            "tags": ["deployment-check"],
            "metadata": {"opik_version": version, "model_called": False},
        },
    )
    # 仅提取编号并拼回本地 API，避免反向代理返回内部地址时误访问其他主机。
    trace_id = str(UUID(headers["Location"].rstrip("/").rsplit("/", 1)[-1]))
    for attempt in range(40):
        try:
            trace, _ = request_api(
                opener, base_url, "GET", f"/v1/private/traces/{trace_id}?strip_attachments=true"
            )
            break
        except HTTPError as error:
            # ClickHouse 刚写入的数据可能稍后才可读，只重试读取，避免重复写入。
            if error.code != 404 or attempt == 39:
                raise
            time.sleep(0.5)
    expected = {
        "name": "local-deployment-smoke",
        "project_id": project["id"],
        "input": expected_input,
        "output": expected_output,
    }
    for field, value in expected.items():
        if trace.get(field) != value:
            raise RuntimeError(f"读回字段 {field} 与写入值不一致：{trace.get(field)!r}")
    print(f"验证通过：{base_url}")
    print(f"项目：{project_name}（{project['id']}）")
    print(f"Trace：{trace_id}，已成功写入并读回输入和输出。")


def main():
    """使用与 Compose 相同的本地端口配置，并允许显式指定验证地址。"""
    config = read_local_config()
    port = os.environ.get("OPIK_PORT", config.get("OPIK_PORT", "5174"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{port}/api")
    parser.add_argument("--project", default="opik-local-demo")
    args = parser.parse_args()
    run_smoke(args.base_url.rstrip("/"), args.project, config.get("OPIK_VERSION", "2.2.151"))


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        print(f"验证失败：HTTP {error.code}，{error.read().decode('utf-8')[:2000]}", file=sys.stderr)
        sys.exit(1)
    except (URLError, RuntimeError, KeyError, ValueError) as error:
        print(f"验证失败：{error}", file=sys.stderr)
        sys.exit(1)
