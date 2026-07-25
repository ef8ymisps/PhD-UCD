$ErrorActionPreference = "Stop"

$Root = Join-Path (Get-Location) "results\own_vs_powermodels"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ArchiveRoot = Join-Path $Root "_source_top_level_archive"
$ArchiveDir = Join-Path $ArchiveRoot $Timestamp

New-Item -ItemType Directory -Path $ArchiveDir -Force | Out-Null

$manifestRows = New-Object System.Collections.Generic.List[object]
$files = Get-ChildItem -LiteralPath $Root -File -Filter "*.csv" | Sort-Object Name

foreach ($file in $files) {
    $destination = Join-Path $ArchiveDir $file.Name
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    $manifestRows.Add([pscustomobject]@{
        file = $file.Name
        source = $file.FullName
        archive_path = $destination
        bytes = $file.Length
        last_write_time = $file.LastWriteTime
    })
}

$manifestPath = Join-Path $ArchiveDir "manifest.csv"
$manifestRows | Export-Csv -LiteralPath $manifestPath -NoTypeInformation

$readme = @'
# Top-Level CSV Archive

This folder is a timestamped copy of the top-level CSV files from
`results/own_vs_powermodels`.

The original files were left in place because many diagnostic scripts still use
those top-level paths as their working/source inputs. Use this archive as a
backup before any later cleanup or path refactor.
'@

Set-Content -LiteralPath (Join-Path $ArchiveDir "README.md") -Value $readme -Encoding UTF8

Write-Host "Archived $($files.Count) CSV files to $ArchiveDir"
Write-Host "Manifest written to $manifestPath"
