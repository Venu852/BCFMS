@echo off
setlocal EnableExtensions
REM ==========================================================================
REM  BCFMS - complete setup (Windows)
REM  Double-click this file, or run it from a command prompt in this folder.
REM    setup.bat         auto-detects an NVIDIA GPU
REM    setup.bat cpu     force the CPU-only install
REM  Needs Python 3.12 (64-bit) installed and an internet connection.
REM ==========================================================================

cd /d "%~dp0"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

REM ---- GPU or CPU ---------------------------------------------------------
set "MODE=gpu"
if /i "%~1"=="cpu" set "MODE=cpu"
if "%MODE%"=="gpu" (
    nvidia-smi >nul 2>&1 || set "MODE=cpu"
)
if "%MODE%"=="gpu" (
    set "TORCH_INDEX=https://download.pytorch.org/whl/cu124"
    set "LLAMA_INDEX=https://abetlen.github.io/llama-cpp-python/whl/cu124"
) else (
    set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
    set "LLAMA_INDEX=https://abetlen.github.io/llama-cpp-python/whl/cpu"
)
echo.
echo ==^> Install mode: %MODE%

REM ---- 1. Virtual environment ---------------------------------------------
if exist ".venv" if not exist "%VENV_PY%" (
    echo ==^> Removing broken .venv
    rmdir /s /q ".venv"
)
if not exist "%VENV_PY%" (
    echo ==^> Creating .venv with Python 3.12
    py -3.12 -m venv .venv 2>nul || python -m venv .venv
)
if not exist "%VENV_PY%" (
    echo [ERROR] Could not create .venv. Install Python 3.12 from https://www.python.org/downloads/
    goto :fail
)

echo ==^> Upgrading pip
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel || goto :fail

REM ---- 2. PyTorch ---------------------------------------------------------
echo ==^> Installing PyTorch (%MODE%)
"%VENV_PY%" -m pip install torch --index-url %TORCH_INDEX% || goto :fail

REM ---- 3. Gemma runtime (prebuilt wheel, no compiler needed) --------------
echo ==^> Installing llama-cpp-python (%MODE%)
"%VENV_PY%" -m pip install llama-cpp-python --only-binary=:all: --extra-index-url %LLAMA_INDEX% || goto :fail

REM ---- 4. Remaining packages ----------------------------------------------
echo ==^> Installing requirements.txt
"%VENV_PY%" -m pip install -r requirements.txt --only-binary=llama-cpp-python --extra-index-url %LLAMA_INDEX% || goto :fail

REM ---- 5. Models (about 3.2 GB, skipped if already present) ---------------
if exist "models\gemma-4-E2B-it-GGUF\gemma-4-E2B-it-Q4_K_M.gguf" goto :gemma_ok
echo ==^> Downloading Gemma 4 E2B model (about 3.1 GB)
"%VENV_PY%" -c "from huggingface_hub import hf_hub_download; hf_hub_download('unsloth/gemma-4-E2B-it-GGUF', 'gemma-4-E2B-it-Q4_K_M.gguf', local_dir='models/gemma-4-E2B-it-GGUF')" || goto :fail
:gemma_ok
if exist "models\all-MiniLM-L6-v2\model.safetensors" goto :embed_ok
echo ==^> Downloading all-MiniLM-L6-v2 embedding model
"%VENV_PY%" -c "from huggingface_hub import snapshot_download; snapshot_download('sentence-transformers/all-MiniLM-L6-v2', local_dir='models/all-MiniLM-L6-v2', allow_patterns=['*.json','*.txt','model.safetensors','1_Pooling/*'])" || goto :fail
:embed_ok
echo ==^> Models ready

REM ---- 6. Settings file and database --------------------------------------
if exist ".env" goto :env_ok
echo ==^> Creating .env from .env.example
copy /y ".env.example" ".env" >nul || goto :fail
if "%MODE%"=="cpu" powershell -NoProfile -Command "(Get-Content .env) -replace '^BCFMS_LLM_GPU_LAYERS=.*','BCFMS_LLM_GPU_LAYERS=0' | Set-Content -Encoding ascii .env"
:env_ok

echo ==^> Applying database migrations
"%VENV_PY%" manage.py migrate || goto :fail

REM ---- 7. Check -----------------------------------------------------------
echo ==^> Verifying install
"%VENV_PY%" -c "import django, torch, llama_cpp; print('Django', django.get_version()); print('PyTorch', torch.__version__, '- GPU available:', torch.cuda.is_available()); print('llama-cpp-python', llama_cpp.__version__)" || goto :fail

echo.
echo ==========================================================
echo  Setup complete.
echo  Start the app :  .venv\Scripts\python.exe manage.py runserver
echo  Then open     :  http://127.0.0.1:8000/
echo ==========================================================
echo.
set /p "RUN=Start the server now? (Y/N) "
if /i "%RUN%"=="Y" "%VENV_PY%" manage.py runserver
pause
exit /b 0

:fail
echo.
echo [ERROR] Setup stopped. Read the message above, fix it, and run setup.bat again.
pause
exit /b 1
