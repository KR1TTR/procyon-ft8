@echo off
rem DEMO ONLY: Procyon with a simulated band - no radio, nothing is transmitted.
rem For real operating, use Procyon.bat instead.
title Procyon - DEMO ONLY (simulated band, no radio)
cd /d "%~dp0"
"%~dp0python\python.exe" -m procyon --demo %*
if errorlevel 1 pause
