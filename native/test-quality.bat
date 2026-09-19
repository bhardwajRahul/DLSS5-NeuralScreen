@echo off
setlocal
cd /d "%~dp0"
call "%~dp0vcvars.bat" || exit /b 1
if not exist ..\_work mkdir ..\_work
cl /nologo /EHsc /W4 /std:c++17 ..\tests\quality_gpu.cpp /Fo:..\_work\quality_gpu.obj /Fe:..\_work\quality_gpu.exe /link d3d11.lib d3dcompiler.lib user32.lib
if errorlevel 1 exit /b 1
..\_work\quality_gpu.exe
exit /b %errorlevel%
