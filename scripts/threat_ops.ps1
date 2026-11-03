<#
.SYNOPSIS
    Threat Lens operator commands against the running stack.

.DESCRIPTION
    Thin wrapper over the admin HTTP API so the demo does not depend on
    remembering Invoke-RestMethod syntax. It logs in, then lists, blocks or
    unblocks an identity.

.EXAMPLE
    .\scripts\threat_ops.ps1

.EXAMPLE
    .\scripts\threat_ops.ps1 -Block 203.0.113.50 -Reason "demo_manual_block"

.EXAMPLE
    .\scripts\threat_ops.ps1 -Unblock 203.0.113.50

.EXAMPLE
    .\scripts\threat_ops.ps1 -List -BaseUrl http://localhost:8000
#>
[CmdletBinding(DefaultParameterSetName = 'List')]
param(
    [Parameter(ParameterSetName = 'List')]
    [switch]$List,

    [Parameter(ParameterSetName = 'Block', Mandatory = $true)]
    [string]$Block,

    [Parameter(ParameterSetName = 'Block')]
    [string]$Reason = 'manual_block_by_admin',

    [Parameter(ParameterSetName = 'Unblock', Mandatory = $true)]
    [string]$Unblock,

    [string]$BaseUrl = 'http://localhost:8000',

    # Local development seed account. Override on any real deployment.
    [string]$Email = 'admin@globelens.ai',
    [string]$Password = 'adminpass123'
)

$ErrorActionPreference = 'Stop'

function Show-Failure {
    param([string]$What, [System.Management.Automation.ErrorRecord]$Err)
    $detail = $Err.ErrorDetails.Message
    if (-not $detail -and $Err.Exception.Response) {
        try {
            $stream = $Err.Exception.Response.GetResponseStream()
            $detail = (New-Object System.IO.StreamReader($stream)).ReadToEnd()
        } catch { }
    }
    if (-not $detail) { $detail = $Err.Exception.Message }
    Write-Host "[FAIL] $What" -ForegroundColor Red
    Write-Host "       $detail" -ForegroundColor DarkGray
    exit 1
}

# 1. Authenticate as an admin.
try {
    $loginBody = @{ email = $Email; password = $Password } | ConvertTo-Json
    $login = Invoke-RestMethod -Uri "$BaseUrl/api/v1/auth/login" -Method Post `
        -ContentType 'application/json' -Body $loginBody
    $token = $login.access_token
    if (-not $token) { throw 'login returned no access_token' }
} catch {
    Show-Failure "login as $Email at $BaseUrl" $_
}

$headers = @{ Authorization = "Bearer $token" }

switch ($PSCmdlet.ParameterSetName) {
    'Block' {
        try {
            $body = @{ ip = $Block; reason = $Reason } | ConvertTo-Json
            $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/admin/threat/blocks" -Method Post `
                -Headers $headers -ContentType 'application/json' -Body $body
            Write-Host "[OK] block issued" -ForegroundColor Green
            Write-Host ($r | ConvertTo-Json -Depth 4)
        } catch {
            Show-Failure "block $Block" $_
        }
    }

    'Unblock' {
        try {
            $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/admin/threat/blocks/$Unblock/unblock" `
                -Method Post -Headers $headers
            Write-Host "[OK] block revoked" -ForegroundColor Green
            Write-Host ($r | ConvertTo-Json -Depth 4)
        } catch {
            Show-Failure "unblock $Unblock (404 means it is not blocked)" $_
        }
    }

    default {
        try {
            $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/admin/threat/blocks?limit=50" -Headers $headers
        } catch {
            Show-Failure 'list blocks' $_
        }

        if ($r.count -eq 0) {
            Write-Host "[i] no blocked identities recorded" -ForegroundColor Yellow
        } else {
            Write-Host "[i] $($r.count) blocked identit$(if ($r.count -eq 1) {'y'} else {'ies'})" -ForegroundColor Cyan
            $table = $r.blocks |
                Select-Object ip, source, reason, risk_score, active, blocked_at |
                Format-Table -AutoSize | Out-String
            Write-Host $table
        }

        if ($r.recent_feed) {
            Write-Host "[i] recent block feed (newest first)" -ForegroundColor Cyan
            $feed = $r.recent_feed | Select-Object -First 10 |
                Select-Object ts, ip, reason | Format-Table -AutoSize | Out-String
            Write-Host $feed
        }
    }
}
