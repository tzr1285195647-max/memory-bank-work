@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 正在创建项目运行环境...
  python -m venv .venv --system-site-packages
)

".venv\Scripts\python.exe" -c "import langgraph, fastapi" 2>nul
if errorlevel 1 (
  echo 正在安装项目依赖...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo.
echo 记忆银行正在启动：http://127.0.0.1:8765
echo 按 Ctrl+C 可以停止服务。
echo.
start "" "http://127.0.0.1:8765"
".venv\Scripts\python.exe" run.py
pause

