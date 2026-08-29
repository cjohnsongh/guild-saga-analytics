$ErrorActionPreference = "Stop"

$workerUrl = "https://guild-saga-webhook-inbox.cjohnson80.workers.dev"
$secretsFile = Join-Path $PSScriptRoot ".env.worker-secrets.local"

function New-RandomToken {
    param([int]$ByteCount = 48)

    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }

    return ([Convert]::ToBase64String($bytes)).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

if (Test-Path $secretsFile) {
    Write-Host "Reusing existing local Worker secrets file:"
    Write-Host "  $secretsFile"
}
else {
    $heliusWebhookAuth = New-RandomToken
    $pipelineToken = New-RandomToken

    @(
        "HELIUS_WEBHOOK_AUTH=$heliusWebhookAuth"
        "PIPELINE_TOKEN=$pipelineToken"
    ) | Set-Content -Path $secretsFile -Encoding ASCII

    Write-Host "Generated two cryptographically-random Worker secrets."
    Write-Host "Saved locally to ignored file:"
    Write-Host "  $secretsFile"
}

Write-Host ""
Write-Host "Uploading secrets to Cloudflare Worker..."
& npx.cmd wrangler secret bulk $secretsFile
if ($LASTEXITCODE -ne 0) {
    throw "Wrangler secret bulk failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Confirming configured secret names..."
& npx.cmd wrangler secret list
if ($LASTEXITCODE -ne 0) {
    throw "Wrangler secret list failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Checking public Worker health endpoint..."
$health = Invoke-RestMethod -Method Get -Uri "$workerUrl/health"
if (-not $health.ok) {
    throw "Worker health endpoint did not return ok=true."
}

Write-Host ""
Write-Host "Worker health: OK"
Write-Host "Local secret values were not printed."
Write-Host "Do not delete .env.worker-secrets.local yet; later setup will reuse these values for Helius and GitHub Actions."
