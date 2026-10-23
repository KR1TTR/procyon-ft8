@echo off
rem Procyon - start. Keep this window open while you use Procyon;
rem closing it (or pressing Ctrl+C) stops Procyon.
title Procyon
cd /d "%~dp0"
"%~dp0python\python.exe" -m procyon %*
if errorlevel 1 pause
