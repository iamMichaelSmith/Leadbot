$ErrorActionPreference = "Stop"

$python = ".\\.venv\\Scripts\\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Virtual env not found at $python. Run: python -m venv .venv"
    exit 1
}

# Ensure we don't skip cached URLs
$env:VISITED_CACHE_ENABLED = "0"

# Clear proxy env vars that can break outbound HTTP requests
$proxyVars = @(
    "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY",
    "http_proxy","https_proxy","all_proxy","no_proxy"
)
foreach ($v in $proxyVars) {
    if (Test-Path "Env:$v") {
        Remove-Item "Env:$v" -ErrorAction SilentlyContinue
    }
}

& $python "run.py"
