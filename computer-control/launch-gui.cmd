@echo off
REM Launch the Computer Control GUI (Mode C). No terminal window stays open.
cd /d "%~dp0"
start "" pythonw control_panel.py
