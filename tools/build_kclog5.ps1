# =============================================================================
# build_kclog5.ps1 - сборка KCLog5.dll (логгер + контроллер скорости)
#
# Использование:  powershell -File tools\build_kclog5.ps1
#
# Особенности:
#   * ссылки берутся из ПАПКИ ИГРЫ (Managed\*.dll) - гарантирует совпадение
#     профиля Unity (.NET 4.x mscorlib);
#   * имя сборки - KCLog5_new (манифест ДЕПЛОЕННОЙ версии; Unity резолвит
#     AssemblyRef по ИМЕНИ ФАЙЛА - KCLog5.dll, манифест не важен, но мы
#     сохраняем его идентичным рабочей сборке);
#   * результат: <game>\Kingdom_Data\Managed\KCLog5.dll (копия после сборки
#     в tools\KCLog5.dll - референс в репозитории).
#
# Требуется: .NET SDK (dotnet), установленная игра.
# =============================================================================
param(
    [string]$GameDir = 'c:\program files (x86)\steam\steamapps\common\Kingdom'
)

$ErrorActionPreference = 'Stop'
$managed = Join-Path $GameDir 'Kingdom_Data\Managed'
$src = Join-Path $PSScriptRoot 'KCLog5.cs'

if (-not (Test-Path (Join-Path $managed 'UnityEngine.dll'))) {
    throw "не найдена папка игры: $managed"
}

# компилятор Roslyn из установленного dotnet SDK
$csc = Get-ChildItem (Join-Path $env:ProgramFiles 'dotnet\sdk\*\Roslyn\bincore\csc.dll') -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $csc) { $csc = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\dotnet\sdk\*\Roslyn\bincore\csc.dll" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 }
if (-not $csc) { throw 'csc.dll не найден - установлен ли dotnet SDK?' }

$outDir = Join-Path $env:TEMP 'kclog5_build'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$out = Join-Path $outDir 'KCLog5_new.dll'     # имя файла = манифест сборки

# /nostdlib+ - ВМЕСТО стандартных библиотек берём библиотеки самой игры
& dotnet $csc.FullName /nologo /nostdlib+ /noconfig /target:library `
    "/out:$out" `
    "/r:$managed\mscorlib.dll" `
    "/r:$managed\System.dll" `
    "/r:$managed\System.Core.dll" `
    "/r:$managed\UnityEngine.dll" `
    $src
if ($LASTEXITCODE -ne 0) { throw "сборка провалена: $LASTEXITCODE" }

# результат должен называться KCLog5.dll (имя файла резолвится игрой)
$final = Join-Path $outDir 'KClog5.dll'
Move-Item -Force $out $final
$size = (Get-Item $final).Length
Write-Host "собрано: $final ($size байт)"

# копии: в репозиторий (референс) и в игру (с заменой - игра должна быть закрыта)
Copy-Item -Force $final (Join-Path $PSScriptRoot 'KClog5.dll')
Write-Host "референс обновлён: tools\KClog5.dll"

$deploy = Join-Path $managed 'KClog5.dll'
if (Get-Process Kingdom -ErrorAction SilentlyContinue) {
    Write-Host "!! ИГРА ЗАПУЩЕНА - $deploy НЕ заменён (закройте игру и скопируйте вручную)"
} else {
    Copy-Item -Force $final $deploy
    Write-Host "задеплоено: $deploy"
}
