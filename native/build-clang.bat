@echo off
rem Max-optimization build of every native target with the LLVM toolchain:
rem clang-cl + lld-link, -O3, ThinLTO, -march=x86-64-v2 (see the arch block
rem below for what that means and how to ask for a different level).
rem Same sources, same include dirs, same link inputs and the same output
rem names as build-host.bat / build-launcher.bat / build-spout-*.bat - only
rem the compiler and the optimization level change. The output names are
rem load-bearing (native\nvngx.dll is the NGX filename contract, see
rem paths.py), which is why they are kept byte-identical.
rem
rem No vcvars needed: clang-cl and lld-link locate the MSVC headers/libs
rem and the Windows SDK on their own. CLANG_DIR is empty by default - the
rem toolchain is found automatically (see the detection order below); set it
rem to a specific LLVM install to override that, either its root or its bin,
rem so the folder an archive extracts to works as it stands.
rem
rem Flag notes:
rem   /clang:-O3   - clang-cl maps its own /O spells to /O2; /clang:
rem                   forwards the GCC-style -O3 straight to the driver.
rem   -march=<level> - the x86-64 microarchitecture level, see the block
rem                   below. A level, not a chip: the code runs on any CPU
rem                   that implements that level, no vendor scheduling model.
rem   -flto=thin   - ThinLTO bitcode; lld-link combines at link time.
rem   -fuse-ld=lld - link with lld-link from the same LLVM bin folder.
rem NDEBUG is deliberately NOT defined: the release build (build-host.bat)
rem keeps the assert() failure rails, and so does this one.
cd /d "%~dp0"
setlocal

rem ---------------------------------------------------------------------------
rem Where clang comes from. CLANG_DIR is empty (or 0) by default = auto-detect:
rem   1. the clang of a Visual Studio install - the "C++ Clang compiler for
rem      Windows" component puts it in <install>\VC\Tools\Llvm\x64\bin, found
rem      through vswhere (native\vswhere.bat, which searches every drive);
rem   2. a clang-cl that is already on PATH.
rem Setting CLANG_DIR wins over both, and it takes the archive root or its
rem bin, whichever is given:
rem   set "CLANG_DIR=G:\Downloads\clang+llvm-22.1.8-x86_64-pc-windows-msvc"
rem   set "CLANG_DIR=G:\Downloads\clang+llvm-22.1.8-x86_64-pc-windows-msvc\bin"
rem Both are the same toolchain: an LLVM archive unpacks to
rem <name>\bin, <name>\include, <name>\lib, and the tools live in bin.
rem Any of the three needs lld-link.exe and llvm-rc.exe next to clang-cl -
rem both the standalone LLVM archive and the VS component ship them.
rem ---------------------------------------------------------------------------
if "%CLANG_DIR%"=="0" set "CLANG_DIR="
if not defined CLANG_DIR goto :find_clang
if exist "%CLANG_DIR%\clang-cl.exe" goto :clang_ready
rem A downloaded archive keeps the tools one level down in bin; accept either
rem level of the path so pasting the folder you extracted works as pasted.
if exist "%CLANG_DIR%\bin\clang-cl.exe" set "CLANG_DIR=%CLANG_DIR%\bin"
if exist "%CLANG_DIR%\clang-cl.exe" goto :clang_ready
echo CLANG_DIR="%CLANG_DIR%" has no clang-cl.exe - auto-detecting instead.
set "CLANG_DIR="

:find_clang
call "%~dp0vswhere.bat"
if not defined VSWHERE goto :path_clang
rem Newest VS install first; x64 toolset, then any other folder of the same
rem toolset (VS also ships Llvm\bin and Llvm\Arm64\bin).
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -property installationPath`) do (
    if exist "%%i\VC\Tools\Llvm\x64\bin\clang-cl.exe" set "CLANG_DIR=%%i\VC\Tools\Llvm\x64\bin"
)
if defined CLANG_DIR goto :clang_ready
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -property installationPath`) do (
    if exist "%%i\VC\Tools\Llvm\bin\clang-cl.exe" set "CLANG_DIR=%%i\VC\Tools\Llvm\bin"
    if exist "%%i\VC\Tools\Llvm\Arm64\bin\clang-cl.exe" set "CLANG_DIR=%%i\VC\Tools\Llvm\Arm64\bin"
)
if defined CLANG_DIR goto :clang_ready

:path_clang
for /f "usebackq delims=" %%i in (`where clang-cl.exe 2^>nul`) do set "CLANG_DIR=%%~dpi"
if defined CLANG_DIR if "%CLANG_DIR:~-1%"=="\" set "CLANG_DIR=%CLANG_DIR:~0,-1%"
if defined CLANG_DIR goto :clang_ready

echo No clang-cl found. One of these fixes it:
echo   install the Visual Studio component "C++ Clang compiler for Windows";
echo   put clang-cl.exe on PATH;
echo   set "CLANG_DIR=<path>\to\the\LLVM\bin\folder".
exit /b 1

:clang_ready
set "CC=%CLANG_DIR%\clang-cl.exe"
rem llvm-rc compiles launcher.rc; the VS toolset ships it next to clang-cl.
rem If it is missing, fall back to Windows' own rc when that is on PATH.
set "RC=%CLANG_DIR%\llvm-rc.exe"
if exist "%RC%" goto :toolchain_ready
set "RC="
for /f "usebackq delims=" %%i in (`where rc.exe 2^>nul`) do set "RC=%%~fi"
:toolchain_ready
echo clang toolchain: %CLANG_DIR%
"%CC%" --version

rem ---------------------------------------------------------------------------
rem Which x86-64 microarchitecture to target. The default is x86-64-v2, the
rem second level of the x86-64 psABI: baseline plus SSE3, SSSE3, SSE4.1,
rem SSE4.2, POPCNT, CMPXCHG16B and LAHF/SAHF. That is a level, not a chip, so
rem the binary still runs on anything meeting the level (Intel Nehalem 2008+,
rem AMD Bulldozer 2011+) instead of being tuned for one vendor's core. clang
rem takes these names directly; clang-cl lists them all with -march= :
rem   x86-64, x86-64-v2, x86-64-v3, x86-64-v4   plus every chip name
rem                                                   (znver1..5, skylake, ...)
rem Override it:
rem   set "CLANG_ARCH=x86-64"      the most portable build
rem   set "CLANG_ARCH=x86-64-v3"   AVX2 + FMA, Haswell 2013+ / Zen 3+
rem   set "CLANG_ARCH=znver3"      Zen 3 scheduling model, AVX2 codegen
rem CLANG_ARCH=0 means the same as x86-64.
rem ---------------------------------------------------------------------------
if not defined CLANG_ARCH set "CLANG_ARCH=x86-64-v2"
if "%CLANG_ARCH%"=="0" set "CLANG_ARCH=x86-64"

rem An old clang that has never heard of the level must not kill the build.
rem Probe it on a trivial file, then fall back to the level every clang has
rem always had, with a warning so nobody mistakes the fallback for the request.
set "PROBE_DIR=%~dp0..\_work"
if not exist "%PROBE_DIR%" mkdir "%PROBE_DIR%"
>"%PROBE_DIR%\arch_probe.c" echo int main(void){return 0;}
"%CC%" /nologo -march=%CLANG_ARCH% /c "%PROBE_DIR%\arch_probe.c" -o "%PROBE_DIR%\arch_probe.obj"
if not errorlevel 1 goto :arch_ok
echo -march=%CLANG_ARCH% is not accepted by the clang in %CLANG_DIR%.
echo Building -march=x86-64 instead. A newer clang, or one of the chip names
echo above, gets the level you asked for.
set "CLANG_ARCH=x86-64"
"%CC%" /nologo -march=x86-64 /c "%PROBE_DIR%\arch_probe.c" -o "%PROBE_DIR%\arch_probe.obj"
if errorlevel 1 exit /b 1
:arch_ok
del "%PROBE_DIR%\arch_probe.*" >nul 2>&1
set "ARCH=-march=%CLANG_ARCH%"
echo target: %CLANG_ARCH%

set "OPT=/clang:-O3 %ARCH% -flto=thin -fuse-ld=lld -fno-trapping-math -fomit-frame-pointer -fstrict-aliasing -fslp-vectorize -ffp-model=fast -fvectorize -funroll-loops"
rem Common compile baseline - every target uses it, only the language
rem standard may differ per target (see the c++20 note on the worker).
set "CXXBASE=/nologo %OPT% /EHsc /W3 /MD"
set "CXX=%CXXBASE% /std:c++17"
set "SPOUT_LIBS=SpoutDX.lib kernel32.lib user32.lib gdi32.lib advapi32.lib ole32.lib d3d11.lib d3d12.lib dxgi.lib d3dcompiler.lib WindowsApp.lib dwmapi.lib"

echo === [1/6] nvngx.dll_ns-forwarder.dll - the NGX escape hatch ===
"%CC%" %CXX% /Iinclude /LD ns_forwarder.cpp ^
    /Fe:nvngx.dll_ns-forwarder.dll ^
    /link kernel32.lib d3d12.lib
if errorlevel 1 exit /b 1
del ns_forwarder.obj >nul 2>&1

echo === [2/6] nvngx.dll - the worker (dlss5-feed-host64 + spout_bridge) ===
rem /std:c++20 here, not c++17: the worker is the only target that pulls in
rem C++/WinRT (Windows Graphics Capture), and winrt/base.h under Clang needs
rem the C++20 <coroutine> header - its C++17 fallback (<experimental/coroutine>)
rem hard-errors "does not support Clang". The worker itself uses no coroutines;
rem this only satisfies cppwinrt's header requirements.
"%CC%" %CXXBASE% /std:c++20 /Iinclude /Isrc dlss5-feed-host64.cpp spout_bridge.cpp ^
    /Fe:nvngx.dll ^
    /link lib\Windows_x86_64\x64\nvsdk_ngx_d.lib %SPOUT_LIBS% version.lib
if errorlevel 1 exit /b 1
del dlss5-feed-host64.obj spout_bridge.obj >nul 2>&1

echo === [3/6] ..\NeuralScreen.exe - the launcher ===
if defined RC goto :compile_rc
rem Neither llvm-rc nor Windows rc found - the launcher is the only target
rem that needs a resource compiler, so the rest is not worth building.
echo No resource compiler (llvm-rc.exe next to clang, or rc.exe on PATH) - cannot build the launcher.
exit /b 1
:compile_rc
"%RC%" /nologo /fo launcher.res launcher.rc
if errorlevel 1 exit /b 1
"%CC%" %CXX% launcher.cpp launcher.res ^
    /Fe:..\NeuralScreen.exe ^
    /link /SUBSYSTEM:WINDOWS shlwapi.lib user32.lib kernel32.lib
if errorlevel 1 exit /b 1
del launcher.obj launcher.res >nul 2>&1

echo === [4/6] Spout sender + receiver ===
"%CC%" %CXX% /Iinclude spout_sender.cpp /Fe:spout_sender.exe /link %SPOUT_LIBS%
if errorlevel 1 exit /b 1
"%CC%" %CXX% /Iinclude spout_receiver.cpp /Fe:spout_receiver.exe /link %SPOUT_LIBS%
if errorlevel 1 exit /b 1
del spout_sender.obj spout_receiver.obj >nul 2>&1

echo === [5/6] Spout checks (spout_check + adapter check + roundtrip) ===
"%CC%" %CXX% /Iinclude spout_compile_check.cpp /Fe:spout_check.exe /link %SPOUT_LIBS%
if errorlevel 1 exit /b 1
"%CC%" %CXX% spout_adapter_check.cpp /Fe:spout_adapter_check.exe /link d3d11.lib dxgi.lib
if errorlevel 1 exit /b 1
"%CC%" %CXX% /Iinclude spout_roundtrip.cpp /Fe:spout_roundtrip.exe /link %SPOUT_LIBS%
if errorlevel 1 exit /b 1
del spout_compile_check.obj spout_adapter_check.obj spout_roundtrip.obj >nul 2>&1

echo === [6/6] done ===
endlocal
echo all targets built: clang-cl -O3 -march=%CLANG_ARCH% -flto=thin -fuse-ld=lld.
exit /b 0
