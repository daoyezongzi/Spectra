@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
set "API_PORT=18631"
set "WEB_PORT=18701"

if /I "%~1"=="--api-loop" goto :api_loop
if /I "%~1"=="--check" goto :check_only

call :check_prereq
if errorlevel 1 goto :fail

call :ensure_env
call :ensure_python_deps
if errorlevel 1 goto :fail

echo [1/3] Starting local Netease API window...
start "Spectra API" cmd.exe /k "call ""%~f0"" --api-loop"

echo [2/3] Waiting for API port %API_PORT%...
call :wait_api

echo [3/3] Starting Spectra Web window...
start "Spectra Web" cmd.exe /k "cd /d ""%ROOT%"" && python -m streamlit run app/main.py --server.port %WEB_PORT%"

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
call :ensure_python_deps
if errorlevel 1 goto :fail
echo Check complete.
goto :end

:api_loop
cd /d "%ROOT%"
set "PORT=%API_PORT%"
echo [INFO] Starting NeteaseCloudMusicApi on port %API_PORT%...
npx.cmd --yes NeteaseCloudMusicApi
echo.
echo [WARN] API exited. Restarting in 2 seconds...
timeout /t 2 >nul
goto :api_loop

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

:ensure_python_deps
echo [INFO] Checking Python dependencies...
python -c "import streamlit, pandas, requests" >nul 2>nul
if not errorlevel 1 (
  echo [OK] Python dependencies are ready.
  exit /b 0
)

if not exist "requirements.txt" (
  echo [ERROR] requirements.txt not found.
  exit /b 1
)

echo [INFO] Installing Python dependencies from requirements.txt...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install Python dependencies.
  exit /b 1
)

python -c "import streamlit, pandas, requests" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python dependencies still unavailable after install.
  exit /b 1
)
echo [OK] Python dependencies installed.
exit /b 0

:wait_api
powershell -NoProfile -Command "$port=%API_PORT%; $ok=$false; foreach($i in 1..20){$t=(Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue).TcpTestSucceeded; if($t){$ok=$true; break}; Start-Sleep -Milliseconds 500}; if($ok){exit 0}else{exit 1}" >nul 2>nul
if errorlevel 1 (
  echo [WARN] API is not ready yet. Web will still start.
) else (
  echo [OK] API is ready at 127.0.0.1:%API_PORT%
)
exit /b 0

:fail
echo.
echo Start failed. Fix the above error and retry.
pause
exit /b 1

:end
endlocal

