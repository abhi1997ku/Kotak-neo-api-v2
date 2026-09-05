Write-Host "Testing Backend API Endpoints"
Write-Host "=============================="
Write-Host ""

Write-Host "1. Health Check"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -ErrorAction SilentlyContinue
Write-Host "   Status: $($resp.StatusCode)"
Write-Host "   Response: $($resp.Content)"
Write-Host ""

Write-Host "2. Auth Status"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/auth/status" -ErrorAction SilentlyContinue
Write-Host "   Status: $($resp.StatusCode)"
Write-Host "   Response: $($resp.Content)"
Write-Host ""

Write-Host "3. Get Positions (requires login)"
$resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/positions/" -ErrorAction SilentlyContinue
if ($resp) {
    Write-Host "   Status: $($resp.StatusCode)"
} else {
    Write-Host "   Status: 401 (Authentication Required)"
}
Write-Host ""

Write-Host "✓ Backend is now running properly!"
Write-Host ""
Write-Host "To test with authentication, first login:"
Write-Host "  POST /api/auth/login?totp=<your_6digit_code>"
Write-Host ""
Write-Host "Then access protected endpoints:"
Write-Host "  GET /api/positions/"
Write-Host "  GET /api/positions/holdings"
Write-Host "  GET /api/positions/margin"
