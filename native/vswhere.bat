@echo off
rem One place that finds vswhere.exe, for vcvars.bat and build-clang.bat.
rem
rem vswhere comes with the Visual Studio Installer, and the Installer folder
rem sits on the system drive whether or not Visual Studio does: this machine
rem has its install in G:\Program Files\Microsoft Visual Studio\22\Community
rem and its vswhere in C:\Program Files (x86)\Microsoft Visual Studio\
rem Installer\. Both halves can also be on neither the system drive nor the
rem same drive, so the search covers every drive letter instead of trusting
rem one. Order:
rem
rem   1. VS_WHERE, if set - either vswhere.exe itself or the folder holding it;
rem   2. %ProgramFiles(x86)% and %ProgramFiles%;
rem   3. "Program Files (x86)" and "Program Files" on every drive letter;
rem   4. vswhere.exe on PATH.
rem
rem On success %VSWHERE% is left set to the full executable path and the script
rem exits 0. On failure it exits 1 with %VSWHERE% empty and prints nothing: the
rem caller says what it was trying to build and what to install or set.
rem
rem One caveat: a drive letter mapped to something that is not answering (a
rem stale network share) can hold up its own existence check. VS_WHERE set to
rem the real path skips the scan completely.

set "VSWHERE="

rem --- 1. an explicit override -----------------------------------------------
if not defined VS_WHERE goto :try_env
call :use "%VS_WHERE%"
if defined VSWHERE goto :found
echo VS_WHERE="%VS_WHERE%" has no vswhere.exe - searching instead.
:try_env

rem --- 2. the system drive, which is where it nearly always is ---------------
call :trypf "%ProgramFiles(x86)%"
if defined VSWHERE goto :found
call :trypf "%ProgramFiles%"
if defined VSWHERE goto :found

rem --- 3. every drive letter --------------------------------------------------
rem Two passes, the 32-bit Program Files first everywhere, then the 64-bit one,
rem so the order never depends on which letter happens to be free.
for %%d in (C D E F G H I J K L M N O P Q R S T U V W X Y Z) do if not defined VSWHERE call :trydrive "%%d:"
if defined VSWHERE goto :found

rem --- 4. PATH ----------------------------------------------------------------
for /f "usebackq delims=" %%i in (`where vswhere.exe 2^>nul`) do call :use "%%~dpi"

:found
if defined VSWHERE exit /b 0
exit /b 1

rem ---------------------------------------------------------------------------
rem :trypf <a Program Files folder> - the Installer lives under it.
rem ---------------------------------------------------------------------------
:trypf
if defined VSWHERE exit /b 0
if exist "%~1\Microsoft Visual Studio\Installer\vswhere.exe" set "VSWHERE=%~1\Microsoft Visual Studio\Installer\vswhere.exe"
exit /b 0

rem ---------------------------------------------------------------------------
rem :trydrive <"X:"> - both Program Files folders of one drive.
rem ---------------------------------------------------------------------------
:trydrive
if defined VSWHERE exit /b 0
if exist "%~1\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" set "VSWHERE=%~1\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if defined VSWHERE exit /b 0
if exist "%~1\Program Files\Microsoft Visual Studio\Installer\vswhere.exe" set "VSWHERE=%~1\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"
exit /b 0

rem ---------------------------------------------------------------------------
rem :use <a file or a folder> - accept VS_WHERE in either form.
rem ---------------------------------------------------------------------------
:use
if exist "%~1\vswhere.exe" set "VSWHERE=%~1\vswhere.exe"
if not defined VSWHERE if exist "%~1" if /i "%~nx1"=="vswhere.exe" set "VSWHERE=%~1"
exit /b 0
