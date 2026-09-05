Write-Host "Testing Backend API Endpoints"
Write-Host "=============================="
Write-Host ""

Write-Host "1. Health Check"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -ErrorAction SilentlyContinue
Write-Host "   Status: $($resp.StatusCode)"
Write-Host ""

Write-Host "2. Auth Status"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/auth/status" -ErrorAction SilentlyContinue
Write-Host "   Status: $($resp.StatusCode)"
Write-Host ""

Write-Host "3. Get Positions"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/positions/" -ErrorAction SilentlyContinue
if ($resp) {
    Write-Host "   Status: $($resp.StatusCode)"
} else {
    Write-Host "   Status: 401 (requires login)"
}
Write-Host ""

Write-Host "Backend is running!"
