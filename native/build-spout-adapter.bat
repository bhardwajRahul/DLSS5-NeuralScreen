@echo off
cd /d "%~dp0"
setlocal
call "%~dp0vcvars.bat" || exit /b 1
cl /nologo /O2 /EHsc /W3 /MD /std:c++17 spout_adapter_check.cpp /Fe:spout_adapter_check.exe /link d3d11.lib dxgi.lib
if errorlevel 1 exit /b 1
endlocal
echo built.
