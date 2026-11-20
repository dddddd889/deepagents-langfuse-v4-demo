FROM python:3.12-slim
# 固定 uv 版本，使用 PyPI 提供的二进制包安装。
RUN python -m pip install --no-cache-dir uv==0.13.6
WORKDIR /app

# 使用镜像中的 Python，并按锁文件安装依赖。
ENV UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-cache

COPY demo.py demo_common.py deepseek_model.py pdf_demo.py pdf_tools.py pdf_media.py pdf_dataset.py ./
# 依赖已在构建时安装，启动时直接使用该环境。
CMD ["uv", "run", "--no-sync", "demo.py"]
