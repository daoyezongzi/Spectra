@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

if /I "%~1"=="--check" goto :check_only

call :check_prereq
if errorlevel 1 goto :fail

call :ensure_env

echo [1/3] Starting local Netease API window...
start "Spectra API" cmd.exe /k "cd /d ""%ROOT%"" && npx.cmd --yes NeteaseCloudMusicApi"

echo [2/3] Waiting for API port 3000...
call :wait_api

echo [3/3] Starting Spectra Web window...
start "Spectra Web" cmd.exe /k "cd /d ""%ROOT%"" && python -m streamlit run app/main.py"

echo.
echo Started:
echo - Spectra API
echo - Spectra Web
echo Keep API window alive while using the app.
goto :end

:check_only
echo Running checks only (no service start)...
call :check_prereq
if errorlevel 1 goto :fail
call :ensure_env
echo Check complete.
goto :end

:check_prereq
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  exit /b 1
)
where npx.cmd >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npx.cmd not found in PATH.
  exit /b 1
)
exit /b 0

:ensure_env
if exist ".env" (
  echo [INFO] Using existing .env
  exit /b 0
)
if exist ".env.example" (
  copy /y ".env.example" ".env" >nul
  echo [INFO] Created .env from .env.example
  exit /b 0
)
echo [WARN] No .env or .env.example found. Continuing with process defaults.
exit /b 0

:wait_api
powershell -NoProfile -Command "$ok=$false; foreach($i in 1..20){$t=(Test-NetConnection -ComputerName 127.0.0.1 -Port 3000 -WarningAction SilentlyContinue).TcpTestSucceeded; if($t){$ok=$true; break}; Start-Sleep -Milliseconds 500}; if($ok){exit 0}else{exit 1}" >nul 2>nul
if errorlevel 1 (
  echo [WARN] API is not ready yet. Web will still start.
) else (
  echo [OK] API is ready at 127.0.0.1:3000
)
exit /b 0

:fail
echo.
echo Start failed. Fix the above error and retry.
pause
exit /b 1

:end
endlocal

