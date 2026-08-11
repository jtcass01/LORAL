<#
Build the paper with MiKTeX. No Perl, so no latexmk: this runs the classic
four-pass sequence directly (pdflatex, bibtex, pdflatex, pdflatex), which is
all this document needs to resolve its citations and cross-references.

    .\build.ps1            build, then report warnings
    .\build.ps1 -Clean     delete aux files first
    .\build.ps1 -Quiet     suppress the warning report

Figures come from src/ml/plot_paper.py and are NOT regenerated here; run that
script if the underlying data changed.
#>
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$bin = "$env:LOCALAPPDATA\Programs\MiKTeX\miktex\bin\x64"
if (-not (Test-Path (Join-Path $bin "pdflatex.exe"))) {
    throw "pdflatex not found under $bin. Is MiKTeX installed?"
}
$env:PATH = "$bin;$env:PATH"

$aux = @("*.aux", "*.bbl", "*.blg", "*.log", "*.out", "*.toc", "*.lof", "*.lot")
if ($Clean) {
    Get-ChildItem $aux -ErrorAction SilentlyContinue | Remove-Item -Force
    Write-Host "cleaned aux files" -ForegroundColor DarkGray
}

# TeX tools write routine notices to stderr, and PowerShell 5.1 turns any
# stderr from a native command into a terminating NativeCommandError under
# ErrorActionPreference=Stop - even when the exe exits 0. So relax the
# preference around each pass and judge success on whether the PDF was
# written, not on the exit code. Do NOT add a 2>&1 redirect here; that is what
# manufactures the ErrorRecord in the first place.
function Invoke-Pass([string]$label, [scriptblock]$cmd) {
    Write-Host "==> $label" -ForegroundColor Cyan
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $cmd | Out-Null } finally { $ErrorActionPreference = $prev }
}

Invoke-Pass "pdflatex (1/3)" { pdflatex -interaction=nonstopmode main.tex }
Invoke-Pass "bibtex"         { bibtex main }
Invoke-Pass "pdflatex (2/3)" { pdflatex -interaction=nonstopmode main.tex }
Invoke-Pass "pdflatex (3/3)" { pdflatex -interaction=nonstopmode main.tex }

if (-not (Test-Path main.pdf)) { throw "build failed: main.pdf was not written" }

$pdf = Get-Item main.pdf
Write-Host ("`nmain.pdf  {0} KB  {1}" -f [math]::Round($pdf.Length / 1KB), $pdf.LastWriteTime) -ForegroundColor Green

if ($Quiet) { return }

$log = Get-Content main.log

# Real errors first: an undefined reference or citation renders as "??" or "[?]"
# in the PDF and is easy to miss by eye.
$undef = $log | Select-String -Pattern "LaTeX Warning: (Reference|Citation).*undefined"
if ($undef) {
    Write-Host "`nUNDEFINED REFERENCES / CITATIONS:" -ForegroundColor Red
    $undef | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
} else {
    Write-Host "no undefined references or citations" -ForegroundColor Green
}

$missing = $log | Select-String -Pattern "Warning--I didn't find a database entry"
if ($missing) {
    Write-Host "`nMISSING BIB ENTRIES:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
}

# Overfull boxes under ~10pt are normal in two-column IEEE text; only the
# larger ones actually show as text running into the margin.
$over = $log | Select-String -Pattern "Overfull \\hbox \((\d+(\.\d+)?)pt" |
    Where-Object { [double]($_.Matches.Groups[1].Value) -gt 10 }
if ($over) {
    Write-Host "`nOVERFULL BOXES over 10pt (text may run into the margin):" -ForegroundColor Yellow
    $over | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
} else {
    Write-Host "no overfull boxes over 10pt" -ForegroundColor Green
}

$counts = "{0} overfull hbox, {1} underfull hbox" -f `
    ($log | Select-String "Overfull \\hbox").Count,
    ($log | Select-String "Underfull \\hbox").Count
Write-Host "`n$counts" -ForegroundColor DarkGray
