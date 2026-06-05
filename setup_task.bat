@echo off
echo === 创建羽毛球自动预订计划任务 ===
echo.

powershell -NoProfile -Command "$action = New-ScheduledTaskAction -Execute 'C:\Python\python.exe' -Argument '\"C:\Users\zr186\Desktop\羽球脚本\booking.py\" --config --now'; $trigger = New-ScheduledTaskTrigger -Daily -At '07:58'; $settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10); Register-ScheduledTask -TaskName 'BadmintonBooking' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force; Write-Host '任务创建成功！'"

echo.
echo 按任意键退出...
pause >nul
