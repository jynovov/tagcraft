@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TagCraft - Chinese to Danbooru tag

set "PYEXE="
set "PYARGS="

rem ---- 1) py launcher ----
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (set "PYEXE=py" & set "PYARGS=-3" & goto :found)

rem ---- 2) python on PATH ----
python -c "import sys" >nul 2>nul
if not errorlevel 1 (set "PYEXE=python" & goto :found)

rem ---- 3) python3 on PATH ----
python3 -c "import sys" >nul 2>nul
if not errorlevel 1 (set "PYEXE=python3" & goto :found)

rem ---- 4) common install locations ----
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
) do (
  if not defined PYEXE if exist %%P (set "PYEXE=%%~fP" & goto :found)
)

echo.
echo  [ERROR] Python not found on this computer.
echo.
echo  Install Python 3.10 or newer: https://www.python.org/downloads/
echo  During setup, CHECK the box "Add python.exe to PATH".
echo.
echo  If you use ComfyUI portable, run this line instead:
echo    ComfyUI_windows_portable\python_embeded\python.exe "%~dp0server.py"
echo.
pause
exit /b 1

:found
echo.
echo  Python  : %PYEXE% %PYARGS%
%PYEXE% %PYARGS% -c "import sys;print(chr(32)*2+chr(86)+chr(101)+chr(114)+chr(115)+chr(105)+chr(111)+chr(110)+chr(32)+chr(58), sys.version.split()[0])"
echo  Folder  : %~dp0
echo.
echo  Starting TagCraft... the browser will open at http://127.0.0.1:8848/
echo  KEEP THIS WINDOW OPEN while you use the tool. Close it to stop the server.
echo.
echo ----------------------------------------------------------------
%PYEXE% %PYARGS% "%~dp0server.py" %*
set "RC=%ERRORLEVEL%"
echo ----------------------------------------------------------------
echo.
echo  TagCraft stopped. exit code = %RC%
if not "%RC%"=="0" echo  ^(non-zero exit code usually means the port is busy or Python is too old^)
echo.
pause
