"""生成本地演示配置，避免把固定密码直接写进示例。"""

import secrets
import uuid
from pathlib import Path


def main() -> None:
    """从模板生成 .env；已存在时保留原配置和密钥。"""
    demo_dir = Path(__file__).resolve().parent
    env_path = demo_dir / ".env"
    if env_path.exists():
        raise SystemExit(".env 已存在，已保留原文件；请直接编辑现有配置")

    # 每个服务使用独立随机密码；十六进制避免连接串转义问题。
    secret_names = {
        "LOGIN_PASSWORD", "POSTGRES_PASSWORD", "REDIS_AUTH", "MINIO_ROOT_PASSWORD",
        "NEXTAUTH_SECRET", "SALT", "ENCRYPTION_KEY", "CLICKHOUSE_PASSWORD",
    }
    replacements = {name: secrets.token_hex(32) for name in secret_names}
    replacements["LANGFUSE_PUBLIC_KEY"] = f"pk-lf-{uuid.uuid4()}"
    replacements["LANGFUSE_SECRET_KEY"] = f"sk-lf-{uuid.uuid4()}"

    lines = []
    for line in (demo_dir / ".env.example").read_text(encoding="utf-8").splitlines():
        key = line.partition("=")[0]
        if key in replacements:
            line = f"{key}={replacements[key]}"
        lines.append(line)
    with env_path.open("x", encoding="utf-8") as env_file:
        env_file.write("\n".join(lines) + "\n")
    env_path.chmod(0o600)
    print("已生成 .env，请填写 OPENAI_API_KEY。")
    print("Langfuse 登录账号：demo@example.com；密码见 .env 的 LOGIN_PASSWORD。")


if __name__ == "__main__":
    main()
