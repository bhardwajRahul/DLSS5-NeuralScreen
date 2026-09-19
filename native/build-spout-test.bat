@echo off
cd /d "%~dp0"
setlocal
call "%~dp0vcvars.bat" || exit /b 1
cl /nologo /O2 /EHsc /W3 /MD /std:c++17 /Iinclude spout_sender.cpp /Fe:spout_sender.exe /link SpoutDX.lib kernel32.lib user32.lib gdi32.lib advapi32.lib ole32.lib d3d11.lib d3d12.lib dxgi.lib d3dcompiler.lib WindowsApp.lib dwmapi.lib
if errorlevel 1 exit /b 1
cl /nologo /O2 /EHsc /W3 /MD /std:c++17 /Iinclude spout_receiver.cpp /Fe:spout_receiver.exe /link SpoutDX.lib kernel32.lib user32.lib gdi32.lib advapi32.lib ole32.lib d3d11.lib d3d12.lib dxgi.lib d3dcompiler.lib WindowsApp.lib dwmapi.lib
if errorlevel 1 exit /b 1
endlocal
echo spout sender+receiver built.
