$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$references = @('System.dll','System.Core.dll','System.Xaml.dll','WPF\PresentationFramework.dll','WPF\PresentationCore.dll','WPF\WindowsBase.dll') | ForEach-Object { '/r:' + (Join-Path $framework $_) }
$name = -join ([char[]](0x4ea4,0x524d,0x4e00,0x67e5))
$binary = Join-Path $root ('.gui-check-' + [guid]::NewGuid() + '.exe')
$stdout = [IO.Path]::GetTempFileName(); $stderr = [IO.Path]::GetTempFileName()
try {
    & (Join-Path $framework 'csc.exe') /nologo /target:exe /codepage:65001 $references ('/r:' + (Join-Path $root ($name + '.exe'))) ('/out:' + $binary) (Join-Path $PSScriptRoot 'Smoke.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Smoke build failed' }
    $process = Start-Process -FilePath $binary -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $timeout = if ($env:JIAOQIAN_GUI_LIVE -eq '1') { 280000 } else { 45000 }
    if (!$process.WaitForExit($timeout)) { $process.Kill(); $process.WaitForExit(); throw 'WPF smoke timed out' }
    Get-Content -LiteralPath $stdout,$stderr
    if ($process.ExitCode -ne 0) { throw 'WPF smoke failed' }
} finally {
    foreach ($file in @($binary,$stdout,$stderr)) { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file } }
}
