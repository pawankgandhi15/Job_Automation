param(
  [ValidateSet('Install','Status','Start')][string]$Action,
  [Parameter(Mandatory=$true)][string]$Root,
  [Parameter(Mandatory=$true)][string]$PythonExecutable,
  [Parameter(Mandatory=$true)][string]$TaskName
)
$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$task = $null
try {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} catch {
  # Only a successful query with no matching task establishes absence.
  # Access errors must reach the caller without replacing saved schedule state.
  if ($_.FullyQualifiedErrorId -notlike 'CmdletizationQuery_NotFound*') { throw }
}
$pythonWindowless = Join-Path (Split-Path -Parent $PythonExecutable) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'The installed Python has no pythonw.exe for hidden daily execution.' }
$arguments = '-m job_agent --root "' + $resolvedRoot + '" daily'
if ($task) {
  if ($task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ne $pythonWindowless -or $task.Actions[0].Arguments -ne $arguments -or $task.Actions[0].WorkingDirectory -ne $resolvedRoot) {
    throw 'A different task already uses this name; it was not replaced.'
  }
}
if ($Action -eq 'Install') {
  $ist = [TimeZoneInfo]::FindSystemTimeZoneById('India Standard Time')
  $nowIst = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $ist)
  $start = $nowIst.Date.AddHours(9)
  if ($start -le $nowIst) { $start = $start.AddDays(1) }
  $boundary = $start.ToString('yyyy-MM-ddTHH:mm:ss') + '+05:30'
  $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $escapedRoot = [Security.SecurityElement]::Escape($resolvedRoot)
  $escapedPython = [Security.SecurityElement]::Escape($pythonWindowless)
  $escapedArguments = [Security.SecurityElement]::Escape($arguments)
  $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Different employers for daily review at 09:00 IST. Discovery and local reports only.</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>$sid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled><Hidden>true</Hidden><ExecutionTimeLimit>PT1H</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>$escapedPython</Command><Arguments>$escapedArguments</Arguments><WorkingDirectory>$escapedRoot</WorkingDirectory></Exec></Actions>
</Task>
"@
  $task = Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force
}
if (-not $task) {
  @{ installed=$false; task_name=$TaskName } | ConvertTo-Json -Compress
  return
}
if ($Action -eq 'Start') { Start-ScheduledTask -TaskName $TaskName }
$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
$nextUtc = if ($info.NextRunTime.Year -gt 2000) { $info.NextRunTime.ToUniversalTime().ToString('o') } else { $null }
$lastUtc = if ($info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToUniversalTime().ToString('o') } else { $null }
@{
  installed=$true; task_name=$TaskName; enabled=($task.State -ne 'Disabled'); state=[string]$task.State;
  next_run_utc=$nextUtc; last_run_utc=$lastUtc; last_result=$info.LastTaskResult;
  schedule='Daily at 09:00 Asia/Kolkata'; requires_signed_in_user=$true;
  executable=$task.Actions[0].Execute; arguments=$task.Actions[0].Arguments
} | ConvertTo-Json -Compress
