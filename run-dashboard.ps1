$ErrorActionPreference = "Stop"

$python = ".\\.venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual env not found at $python. Run: python -m venv .venv"
    exit 1
}

$envPath = ".\\.env"
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line) { return }
        if ($line.StartsWith("#")) { return }
        $parts = $line.Split("=", 2)
        if ($parts.Length -ne 2) { return }
        $key = $parts[0].Trim()
        $val = $parts[1].Trim()
        if ($key) { Set-Item -Path ("Env:" + $key) -Value $val }
    }
}

$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:NO_PROXY = ""
$env:http_proxy = ""
$env:https_proxy = ""
$env:all_proxy = ""
$env:no_proxy = ""

if (-not $env:DASHBOARD_SESSION_SECRET) {
    Write-Error "Missing DASHBOARD_SESSION_SECRET in .env"
    exit 1
}

if (-not $env:DASHBOARD_USERS) {
    Write-Error "Missing DASHBOARD_USERS in .env (format: user:pass,user2:pass2)"
    exit 1
}

$port = $env:DASHBOARD_PORT
if (-not $port) { $port = "8001" }

& $python -m uvicorn dashboard_app:app --host 0.0.0.0 --port $port
