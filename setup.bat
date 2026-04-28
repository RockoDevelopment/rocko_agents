@echo off
echo Setting up ThePaperTeam...
conda env create -f environment.yml
conda activate hummingbot
echo.
echo Setup complete. Run start.bat to launch.
pause