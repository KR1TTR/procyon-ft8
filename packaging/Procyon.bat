@echo off
rem Procyon FT8 - start. Keep this window open while you use Procyon;
rem closing it (or pressing Ctrl+C) stops Procyon.
title Procyon FT8
cd /d "%~dp0"
"%~dp0python\python.exe" -m procyon %*
if errorlevel 1 pause
