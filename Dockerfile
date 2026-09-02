# 中草药多模态识别系统 - 部署镜像
# 构建：docker build -t herb-recognition:latest .
# 运行：docker run --gpus all -p 8000:8000 -e CKPT=experiments/checkpoints/best_model.pth \
#        -v "%cd%/experiments/checkpoints:/app/experiments/checkpoints" herb-recognition:latest
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# 基础依赖（用于 Pillow / 图像解码）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖清单（利用层缓存）
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 再复制源码
COPY . .

# 默认权重与 BERT 通过挂载提供；此处给出可覆盖的默认值
ENV CKPT=experiments/checkpoints/best_model.pth \
    BERT_MODEL_PATH=/app/models/bert-base-chinese

EXPOSE 8000

# 默认以 Web 服务模式启动
CMD ["python", "main.py", "--mode", "serve", "--port", "8000"]
