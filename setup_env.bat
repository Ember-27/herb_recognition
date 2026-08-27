@echo off
REM 中草药识别项目 - Windows 环境一键安装 (PowerShell / CMD 均可运行)
REM 说明：创建 venv，安装 CUDA 11.8 版 PyTorch，再安装其余依赖

setlocal
set VENV_DIR=venv
set PYTHON=python

echo [1/4] 创建虚拟环境 %VENV_DIR% ...
if not exist %VENV_DIR% (
    %PYTHON% -m venv %VENV_DIR%
) else (
    echo 虚拟环境已存在，跳过创建。
)

call %VENV_DIR%\Scripts\activate.bat

echo [2/4] 升级 pip ...
python -m pip install --upgrade pip

echo [3/4] 安装 CUDA 11.8 版 PyTorch (适配 8G 显存) ...
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

echo [4/4] 安装其余依赖 ...
pip install -r requirements.txt

echo.
echo ========================================
echo  安装完成！激活环境：
echo      %VENV_DIR%\Scripts\activate.bat
echo  检查环境：
echo      python check_environment.py
echo  启动演示：
echo      python main.py --mode demo
echo ========================================
endlocal
pause
