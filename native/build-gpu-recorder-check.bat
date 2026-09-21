@echo off
rem gpu_recorder_check.exe - the GPU recorder alone on this machine's card:
rem no worker, no NGX. tests\test_gpu_recorder.py builds and runs it.
rem
rem   build-gpu-recorder-check.bat [output folder]    (default: this folder)
rem
rem Prints NO_COMPILER when there is no Visual Studio to build with, so the
rem test can tell "cannot build here" from "the build is broken".
setlocal
set "SRC=%~dp0"
if "%~1"=="" (set "OUT=%~dp0.") else (set "OUT=%~f1")
if not exist "%OUT%" mkdir "%OUT%"
call "%~dp0vcvars.bat" || (echo NO_COMPILER& exit /b 1)
cl /nologo /O2 /EHsc /W3 /MD /std:c++17 /Fo"%OUT%\\" ^
   "%SRC%gpu_recorder_check.cpp" "%SRC%gpu_recorder.cpp" ^
   /Fe:"%OUT%\gpu_recorder_check.exe" ^
   /link d3d12.lib d3d11.lib dxgi.lib mfplat.lib mfreadwrite.lib mfuuid.lib ole32.lib
if errorlevel 1 exit /b 1
endlocal
