@echo off
rem 重新编译启动器 TagCraft.exe（把 assets\tagcraft.ico 作为图标写进 exe 资源）
rem 双击本文件即可。需要系统自带的 .NET Framework 编译器（Windows 都有）。

setlocal
cd /d "%~dp0"

set "CSC=%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%SystemRoot%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo [ERROR] 找不到 csc.exe，请确认系统装了 .NET Framework 4.x
  pause
  exit /b 1
)

if not exist "assets\tagcraft.ico" (
  echo [WARN] 没有 assets\tagcraft.ico，将编译一个无自定义图标的 exe
  "%CSC%" /nologo /target:winexe /out:TagCraft.exe /r:System.Windows.Forms.dll launcher.cs
) else (
  "%CSC%" /nologo /target:winexe /out:TagCraft.exe /win32icon:assets\tagcraft.ico /r:System.Windows.Forms.dll launcher.cs
)

if errorlevel 1 (
  echo [ERROR] 编译失败
  pause
  exit /b 1
)

echo.
echo 编译完成：TagCraft.exe
pause
