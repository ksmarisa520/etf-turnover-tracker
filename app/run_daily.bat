@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   宽基ETF量价跟踪器 - 数据更新
echo   时间: %date% %time%
echo ============================================
echo.
python update_data.py
if errorlevel 1 (
  echo.
  echo [失败] 请检查网络，或先用 --test 测一下接口
) else (
  echo.
  echo [完成] 双击 index.html 查看，或刷新已打开的页面
)
echo.
pause
