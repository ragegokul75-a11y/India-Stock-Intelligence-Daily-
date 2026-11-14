$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OutputDirectory = Join-Path $ProjectRoot 'sample_data'
$OutputFile = Join-Path $OutputDirectory 'sample_market_data.csv'
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$StartDate = [datetime]::ParseExact('2025-08-01', 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$EndDate = [datetime]::ParseExact('2026-11-06', 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$TradingDates = [System.Collections.Generic.List[datetime]]::new()
for ($Day = $StartDate; $Day -le $EndDate; $Day = $Day.AddDays(1)) {
    if ($Day.DayOfWeek -ne [DayOfWeek]::Saturday -and $Day.DayOfWeek -ne [DayOfWeek]::Sunday) {
        $TradingDates.Add($Day)
    }
}

$Rows = [System.Collections.Generic.List[string]]::new()
$Rows.Add('Exchange,Symbol,Company Name,Security ID,Security Type,Listing Status,Date,Open,High,Low,Close,Volume,Source Timestamp,Source')
$Securities = @(
    @{ Exchange='NSE'; Symbol='ORIONSAMP'; Name='Sample Orion Holdings'; Id='SAMPLE-SEC-001'; Type='Equity'; Lag=0; Base=155.0; Trend=0.12; Phase=1.0; Drop=$true },
    @{ Exchange='BSE'; Symbol='ORIONSAMP'; Name='Sample Orion Holdings'; Id='SAMPLE-SEC-001'; Type='Equity'; Lag=0; Base=152.0; Trend=0.10; Phase=2.2; Drop=$false },
    @{ Exchange='BSE'; Symbol='ORIONSME'; Name='Sample Micro SME Limited'; Id='SAMPLE-SME-002'; Type='SME Equity'; Lag=12; Base=42.0; Trend=0.07; Phase=3.1; Drop=$false }
)
$Culture = [Globalization.CultureInfo]::InvariantCulture
for ($Index = 0; $Index -lt $TradingDates.Count; $Index++) {
    $TradeDate = $TradingDates[$Index]
    foreach ($Security in $Securities) {
        if ($Security.Lag -gt 0 -and $Index -ge ($TradingDates.Count - $Security.Lag)) { continue }
        $Price = $Security.Base + ($Index * $Security.Trend) + ([math]::Sin($Index / 8.0 + $Security.Phase) * 2.0)
        if ($Security.Drop -and $Index -ge 180) { $Price *= 0.52 }
        $OpenPrice = $Price - 0.15
        $HighPrice = $Price + 0.90
        $LowPrice = $Price - 0.85
        $Volume = 25000 + ($Index * 31) + [int]([math]::Abs([math]::Sin($Index / 5.0)) * 8000)
        $Values = @(
            $Security.Exchange, $Security.Symbol, $Security.Name, $Security.Id, $Security.Type, 'Active',
            $TradeDate.ToString('yyyy-MM-dd', $Culture),
            $OpenPrice.ToString('F2', $Culture), $HighPrice.ToString('F2', $Culture),
            $LowPrice.ToString('F2', $Culture), $Price.ToString('F2', $Culture), $Volume,
            ($TradeDate.ToString('yyyy-MM-dd', $Culture) + 'T17:00:00+05:30'), 'SAMPLE_SYNTHETIC'
        )
        $Rows.Add(($Values -join ','))
    }
}
[IO.File]::WriteAllLines($OutputFile, $Rows, [Text.UTF8Encoding]::new($false))
Write-Host "Created synthetic sample file: $OutputFile"
