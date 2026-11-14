# Launch-SE.ps1 - Dev-Toolbox CLI entry for Script Editor.
#   Starts the local web editor in this console and opens the browser.
#   Ctrl+C stops the server.
# Usage: .\Launch-SE.ps1 [-Port 8785]
param([int]$Port = 0)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$argv = @{ OpenBrowser = $true }
if ($Port -gt 0) { $argv.Port = $Port }
& (Join-Path $here 'Script-Web.ps1') @argv
exit $LASTEXITCODE
