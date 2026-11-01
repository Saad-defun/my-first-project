@echo off
rem Builds dist\AItoCAD.exe, a single portable file (UI, and the MCP server with --mcp).
rem Run from this folder. Needs Python 3.10+ with: pip install -r requirements-build.txt
cd /d "%~dp0"

python -m PyInstaller --noconfirm --clean --onefile --console --name AItoCAD ^
  --paths . ^
  --collect-submodules ezdxf ^
  --collect-submodules mcp.server --exclude-module mcp.cli ^
  --hidden-import win32com.client --hidden-import pythoncom --hidden-import pywintypes ^
  --hidden-import server --hidden-import ui.window ^
  --add-data "..\prompts\system-prompt.md;prompts" ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQml ^
  app.py
if errorlevel 1 exit /b 1

echo.
echo Built: %~dp0dist\AItoCAD.exe
echo settings.ini is created beside the exe on first start.
