$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$references = @('System.dll','System.Core.dll','System.Web.Extensions.dll','System.Windows.Forms.dll','System.Xaml.dll','WPF\PresentationFramework.dll','WPF\PresentationCore.dll','WPF\WindowsBase.dll') | ForEach-Object { '/r:' + (Join-Path $framework $_) }
$exe = Join-Path $root ((-join ([char[]](0x4ea4,0x524d,0x4e00,0x67e5))) + '.exe')
& (Join-Path $framework 'csc.exe') /nologo /target:winexe /optimize+ /codepage:65001 $references ("/resource:" + (Join-Path $PSScriptRoot 'Main.xaml') + ',Main.xaml') ("/out:" + $exe) (Join-Path $PSScriptRoot 'Workbench.cs')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output $exe
