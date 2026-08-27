#requires -Version 5.1
$ErrorActionPreference = "Stop"

$Project = Join-Path $PSScriptRoot "OpenWorker.WinUIBubbles.csproj"
$Dotnet = (Get-Command dotnet -ErrorAction SilentlyContinue).Source
if (-not $Dotnet) {
    $Dotnet = "C:\Program Files\dotnet\dotnet.exe"
}
if (-not (Test-Path $Dotnet)) {
    throw "dotnet SDK not found"
}

& $Dotnet publish $Project -c Release -r win-x64 -o (Join-Path $PSScriptRoot "publish")
if ($LASTEXITCODE -ne 0) {
    throw "WinUI 3 bubble host publish failed (exit $LASTEXITCODE)"
}
