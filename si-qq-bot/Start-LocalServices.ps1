$ErrorActionPreference = 'Stop'

function Test-SiPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { return $client.ConnectAsync('127.0.0.1', $Port).Wait(700) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

if (-not (Test-SiPort 11434)) {
    $siPrevious = @{}
    $siEnvironment = @{
        OLLAMA_MODELS = 'E:\Ollama\models'
        OLLAMA_HOST = '127.0.0.1:11434'
        OLLAMA_KEEP_ALIVE = '5m'
        TEMP = 'E:\Ollama\tmp'
        TMP = 'E:\Ollama\tmp'
    }
    try {
        foreach ($key in $siEnvironment.Keys) {
            $siPrevious[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
            [Environment]::SetEnvironmentVariable($key, $siEnvironment[$key], 'Process')
        }
        Start-Process -FilePath 'E:\Ollama\ollama.exe' -ArgumentList 'serve' -WindowStyle Hidden
    } finally {
        foreach ($key in $siEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $siPrevious[$key], 'Process')
        }
    }
}

if (-not (Test-SiPort 3900)) {
    $siLocationFile = Join-Path $env:APPDATA 'VoiceStudio\runtime-location.json'
    $siRuntime = $null
    if (Test-Path -LiteralPath $siLocationFile) {
        $siRuntime = (Get-Content -LiteralPath $siLocationFile -Raw -Encoding UTF8 | ConvertFrom-Json).root
    }
    $siBackendRunning = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'python.exe' -and $_.CommandLine -match 'uvicorn.*main:app.*--port 3900'
    }
    if ($siRuntime -and -not $siBackendRunning -and
        (Test-Path -LiteralPath (Join-Path $siRuntime 'project\backend\main.py')) -and
        (Test-Path -LiteralPath (Join-Path $siRuntime 'project\.venv\Scripts\python.exe'))) {
        Start-Process -FilePath (Join-Path $siRuntime 'project\.venv\Scripts\python.exe') `
            -ArgumentList '-m','uvicorn','main:app','--app-dir','backend','--host','127.0.0.1','--port','3900' `
            -WorkingDirectory (Join-Path $siRuntime 'project') -WindowStyle Hidden
    } elseif (-not $siRuntime -and -not (Get-Process -Name VoiceStudio -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath "$env:LOCALAPPDATA\Programs\VoiceStudio\VoiceStudio.exe" -WindowStyle Hidden
    }
}

if (-not (Test-SiPort 3001)) {
    if (-not (Get-Process -Name NapCatQQ-Desktop -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath 'E:\a\NapCatQQ-Desktop.exe' -WindowStyle Hidden
    }
}
