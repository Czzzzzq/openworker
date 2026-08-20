@echo off
rem OpenWorker 悬浮窗启动器（无控制台窗口）
rem 双击本文件即可启动/重启；--replace 会自动替换已在运行的旧实例（拿到新代码）
rem 右键悬浮窗 -> 退出 来关闭
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0floating_icon.py" --replace
) else (
    start "" python.exe "%~dp0floating_icon.py" --replace
)
