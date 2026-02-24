@echo off
REM Launch the Octobatch TUI dashboard
REM Automatically activates the virtual environment if present

if exist "%~dp0.venv\Scripts\activate.bat" (
    call "%~dp0.venv\Scripts\activate.bat"
)

python "%~dp0scripts\tui.py" %*
