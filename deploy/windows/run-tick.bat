@echo off
REM Windows: schedule this every 15 minutes with Task Scheduler, e.g. (run once in an admin prompt,
REM replacing C:\clipping with your clone path):
REM   schtasks /Create /SC MINUTE /MO 15 /TN "ClipFactory" /TR "C:\clipping\deploy\windows\run-tick.bat"
cd /d %~dp0\..\..
set CLIPFACTORY_HOME=%CD%
call .venv\Scripts\clipfactory.exe tick
