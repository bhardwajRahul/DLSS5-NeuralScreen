@echo off
rem One place that finds vcvars64.bat for every build-*.bat in this folder.
rem
rem The compiler comes from wherever Visual Studio actually is on this
rem machine (vswhere), not from the hardcoded BuildTools path that only the
rem original dev box had - the same resolution test_quality_gpu.py uses.
rem A machine with VS Community/Professional/Enterprise, or BuildTools in a
rem different drive, has no such path, and every build script used to die with
rem "The system cannot find the path specified." followed by
rem "'cl' is not recognized as an internal or external command".
rem
rem vswhere itself is located by native\vswhere.bat, which looks on every
rem drive; set VS_WHERE there to say where it is. See that file for the order.
rem
rem Call it, then check:  call "%~dp0vcvars.bat" || exit /b 1
rem On success %VSPATH% is left set for the caller.

call "%~dp0vswhere.bat"
if not defined VSWHERE (
    echo vswhere not found - no Visual Studio with C++ tools on any drive.
    exit /b 1
)
set "VSPATH="
rem Two passes, and the order matters: a released Visual Studio first, a
rem prerelease one only if there is nothing else. vswhere refuses to report a
rem prerelease install unless -prerelease is passed, so on a machine whose
rem only C++ tools are an Insiders/Preview build the first query answers
rem nothing at all and every build script dies with "build tools not found" -
rem even though vcvars64.bat is sitting right there. Passing -prerelease
rem first would have the opposite problem: a Preview install would win over a
rem released one that is also present. Hence: released, then prerelease.
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSPATH=%%i"
if not defined VSPATH for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSPATH=%%i"
if not defined VSPATH (
    echo Visual Studio C++ build tools not found via vswhere.
    exit /b 1
)
call "%VSPATH%\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
exit /b 0
