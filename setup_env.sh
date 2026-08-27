#!/usr/bin/env bash
# 中草药识别项目 - Linux / macOS 环境一键安装
set -e

VENV_DIR="venv"
PYTHON="${PYTHON:-python3}"

echo "[1/4] 创建虚拟环境 ${VENV_DIR} ..."
if [ ! -d "${VENV_DIR}" ]; then
    ${PYTHON} -m venv "${VENV_DIR}"
else
    echo "虚拟环境已存在，跳过创建。"
fi

source "${VENV_DIR}/bin/activate"

echo "[2/4] 升级 pip ..."
python -m pip install --upgrade pip

echo "[3/4] 安装 CUDA 11.8 版 PyTorch (若无 GPU 会自动回退 CPU 版，见下方注释) ..."
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

echo "[4/4] 安装其余依赖 ..."
pip install -r requirements.txt

echo ""
echo "========================================"
echo " 安装完成！激活环境："
echo "     source ${VENV_DIR}/bin/activate"
echo " 检查环境："
echo "     python check_environment.py"
echo " 启动演示："
echo "     python main.py --mode demo"
echo "========================================"
