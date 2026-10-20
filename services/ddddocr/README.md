# 已并入 monorepo

ddddocr 滑块 OCR 已作为 **`services/ddddocr`** 模块，依赖与安装见仓库根目录 **`pyproject.toml`**。

```bash
# 仓库根目录
pip install -e ".[ddddocr]"
rushbuy-ddddocr --host 127.0.0.1 --port 8000
# 或
uvicorn services.ddddocr.main:app --host 127.0.0.1 --port 8000
```

Docker（在仓库根执行）：

```bash
docker build -f services/ddddocr/Dockerfile -t rushbuy-ddddocr .
```

本目录仅保留说明；勿再使用 `app.main:app` 或独立 `ddddocr-fastapi/pyproject.toml`。
