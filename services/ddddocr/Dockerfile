# 在仓库根目录构建: docker build -f services/ddddocr/Dockerfile .
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY services/__init__.py services/__init__.py
COPY services/ddddocr services/ddddocr

RUN pip install --no-cache-dir -e ".[ddddocr]"

EXPOSE 8000
CMD ["uvicorn", "services.ddddocr.main:app", "--host", "0.0.0.0", "--port", "8000"]
