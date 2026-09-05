$url = "http://127.0.0.1:8000/api/positions/"

try {
    $response = Invoke-WebRequest -Uri $url -ErrorAction Stop
    Write-Host "Success! Status: $($response.StatusCode)"
    Write-Host "Response: $($response.Content)"
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    Write-Host "Error Status Code: $statusCode"
    
    $stream = $_.Exception.Response.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $content = $reader.ReadToEnd()
    $reader.Close()
    
    Write-Host "Error Response:"
    Write-Host $content
}
