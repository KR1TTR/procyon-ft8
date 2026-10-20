@echo off
rem Procyon FT8 with a simulated band - nothing is transmitted.
title Procyon FT8 (demo)
cd /d "%~dp0"
"%~dp0python\python.exe" -m procyon --demo %*
if errorlevel 1 pause
