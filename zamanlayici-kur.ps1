# Haftalik gorevi Windows Gorev Zamanlayici'ya kaydeder: Pazar 21:00, otomatik.bat.
# PC kapaliysa acilinca calisir (StartWhenAvailable), uykudaysa uyandirir (WakeToRun).
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat  = Join-Path $root "otomatik.bat"
$name = "Shorts Fabrikasi - Haftalik"
$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ('/c "' + $bat + '"') -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 21:00
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host ""
Write-Host "Kuruldu: '$name' -> her Pazar 21:00, $bat"
Write-Host "Kontrol: Gorev Zamanlayici (taskschd.msc) > Gorev Zamanlayici Kitapligi"
Write-Host "Simdi denemek icin: schtasks /run /tn `"$name`""
Write-Host "Kaldirmak icin:     schtasks /delete /tn `"$name`" /f"
