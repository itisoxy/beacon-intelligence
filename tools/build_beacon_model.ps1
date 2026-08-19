param(
  [string]$DataDir = ".\Data",
  [string]$OutDir = ".\public\data"
)

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-ZipEntryText { param($Zip, [string]$Name)
  $entry = $Zip.GetEntry($Name)
  if (-not $entry) { return $null }
  $reader = [System.IO.StreamReader]::new($entry.Open())
  try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}
function Get-ColumnIndex { param([string]$CellRef)
  $letters = ($CellRef -replace '[0-9]', '').ToUpperInvariant(); $n = 0
  foreach ($ch in $letters.ToCharArray()) { $n = ($n * 26) + ([int][char]$ch - [int][char]'A' + 1) }
  return $n
}
function Convert-CellValue { param($Cell, $SharedStrings)
  $type = $Cell.GetAttribute('t')
  $valueNode = $Cell.SelectSingleNode("*[local-name()='v']")
  if ($type -eq 'inlineStr') {
    $texts = @(); foreach ($t in $Cell.SelectNodes(".//*[local-name()='t']")) { $texts += [string]$t.InnerText }
    return ($texts -join '')
  }
  if ($null -eq $valueNode) { return $null }
  $raw = [string]$valueNode.InnerText
  if ($type -eq 's') {
    $idx = 0; if ([int]::TryParse($raw, [ref]$idx) -and $idx -lt $SharedStrings.Count) { return $SharedStrings[$idx] }
  }
  if ($type -eq 'b') { return $raw -eq '1' }
  $num = 0.0
  if ([double]::TryParse($raw, [Globalization.NumberStyles]::Any, [Globalization.CultureInfo]::InvariantCulture, [ref]$num)) { return $num }
  return $raw
}
function Get-SharedStrings { param($Zip)
  $items = [System.Collections.Generic.List[string]]::new()
  $xmlText = Get-ZipEntryText $Zip 'xl/sharedStrings.xml'; if (-not $xmlText) { return $items }
  [xml]$xml = $xmlText
  foreach ($si in $xml.SelectNodes("//*[local-name()='si']")) {
    $parts = @(); foreach ($t in $si.SelectNodes(".//*[local-name()='t']")) { $parts += [string]$t.InnerText }
    $items.Add(($parts -join '')) | Out-Null
  }
  return $items
}
function Get-SheetMap { param($Zip)
  [xml]$workbook = Get-ZipEntryText $Zip 'xl/workbook.xml'
  [xml]$rels = Get-ZipEntryText $Zip 'xl/_rels/workbook.xml.rels'
  $relMap = @{}
  foreach ($rel in $rels.SelectNodes("//*[local-name()='Relationship']")) {
    $target = [string]$rel.Target
    $relMap[$rel.Id] = if ($target.StartsWith('/')) { $target.TrimStart('/') } else { 'xl/' + $target.TrimStart('/') }
  }
  $sheets = @()
  foreach ($sheet in $workbook.SelectNodes("//*[local-name()='sheet']")) {
    $rid = $sheet.GetAttribute('id', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')
    $sheets += [pscustomobject]@{ name = [string]$sheet.name; path = $relMap[$rid] }
  }
  return $sheets
}
function Read-SheetRows { param($Zip, [string]$Path, $SharedStrings)
  [xml]$xml = Get-ZipEntryText $Zip $Path
  $rows = @()
  foreach ($row in $xml.SelectNodes("//*[local-name()='sheetData']/*[local-name()='row']")) {
    $cells = @{}; $maxCol = 0
    foreach ($c in $row.SelectNodes("*[local-name()='c']")) {
      $idx = Get-ColumnIndex ([string]$c.r); $maxCol = [Math]::Max($maxCol, $idx)
      $cells[$idx] = Convert-CellValue $c $SharedStrings
    }
    $values = @(); for ($i = 1; $i -le $maxCol; $i++) { $values += $cells[$i] }
    $rows += ,$values
  }
  return $rows
}
function Rows-ToObjects { param($Rows, [string]$SourceFile, [string]$SheetName, [int]$HeaderRow = 1)
  if ($Rows.Count -lt $HeaderRow) { return @() }
  $headers = $Rows[$HeaderRow - 1]
  $objects = @()
  for ($i = $HeaderRow; $i -lt $Rows.Count; $i++) {
    $row = $Rows[$i]
    $nonempty = @($row | Where-Object { $null -ne $_ -and "$_" -ne '' })
    if ($nonempty.Count -eq 0) { continue }
    $o = [ordered]@{}
    for ($c = 0; $c -lt $headers.Count; $c++) {
      $h = [string]$headers[$c]
      if ($h) { $o[$h] = if ($c -lt $row.Count) { $row[$c] } else { $null } }
    }
    $sourceRow = $i + 1
    $o['_provenance'] = [ordered]@{
      source_file = $SourceFile
      source_sheet = $SheetName
      source_row = $sourceRow
      source_cells = "A$sourceRow"
    }
    $objects += [pscustomobject]$o
  }
  return $objects
}
function File-Date { param([string]$Name) return $Name.Substring(0,4) + '-' + $Name.Substring(4,2) + '-' + $Name.Substring(6,2) }
function Row-Key { param($row, [string]$sheet)
  $parts = @($sheet, $row.Quarter, $row.QuarterEndDate, $row.FundCode)
  if ($row.PSObject.Properties.Name -contains 'AssetClassLevel1') { $parts += $row.AssetClassLevel1 }
  if ($row.PSObject.Properties.Name -contains 'ManagerName') { $parts += $row.ManagerName }
  if ($row.PSObject.Properties.Name -contains 'CashFlowCategory') { $parts += $row.CashFlowCategory }
  if ($row.PSObject.Properties.Name -contains 'FlowType') { $parts += $row.FlowType }
  if ($row.PSObject.Properties.Name -contains 'BenchmarkName') { $parts += $row.BenchmarkName }
  return ($parts -join '|')
}
function Canonicalize { param($Rows, [string]$SheetName)
  $groups = @{}
  foreach ($r in $Rows) {
    $key = Row-Key $r $SheetName
    if (-not $groups.ContainsKey($key)) { $groups[$key] = @() }
    $groups[$key] += $r
  }
  $canonical = @(); $dups = @()
  foreach ($key in $groups.Keys) {
    $items = @($groups[$key])
    if ($items.Count -gt 1) {
      $dups += [pscustomobject]@{ key = $key; count = $items.Count; files = @($items | ForEach-Object { $_._provenance.source_file }) }
    }
    $match = @($items | Where-Object { (File-Date $_._provenance.source_file) -eq $_.QuarterEndDate } | Select-Object -First 1)
    $chosen = if ($match.Count -gt 0) { $match[0] } else { ($items | Sort-Object { $_._provenance.source_file } | Select-Object -Last 1) }
    $chosen | Add-Member -NotePropertyName source_record_id -NotePropertyValue $key -Force
    $canonical += $chosen
  }
  return @{ rows = @($canonical | Sort-Object QuarterEndDate, FundCode); duplicates = $dups }
}

$books = @{}; $all = @{ Fund_Summary=@(); Asset_Allocation=@(); Manager_Detail=@(); Cash_Flow_Detail=@(); Benchmarks_Reference=@(); RAW_Export_Extract=@() }
foreach ($file in Get-ChildItem -LiteralPath $DataDir -Filter '*.xlsx' | Sort-Object Name) {
  $zip = [System.IO.Compression.ZipFile]::OpenRead($file.FullName)
  try {
    $shared = Get-SharedStrings $zip; $sheetMap = Get-SheetMap $zip
    $book = [ordered]@{ file = $file.Name; as_of = File-Date $file.Name; sheets = @(); readme = @() }
    foreach ($s in $sheetMap) {
      $rows = Read-SheetRows $zip $s.path $shared
      $book.sheets += [pscustomobject]@{
        name = $s.name
        rows = $rows.Count
        cols = (($rows | ForEach-Object { $_.Count } | Measure-Object -Maximum).Maximum)
        header = if ($rows.Count -gt 0) { $rows[0] } else { @() }
      }
      if ($s.name -eq 'ReadMe') {
        $book.readme = @($rows | ForEach-Object { ($_ | Where-Object { $null -ne $_ -and "$_" -ne '' }) -join ' ' } | Where-Object { $_ })
      } elseif ($all.ContainsKey($s.name)) {
        $headerRow = if ($s.name -eq 'RAW_Export_Extract') { 10 } else { 1 }
        $all[$s.name] += Rows-ToObjects $rows $file.Name $s.name $headerRow
      }
    }
    $books[$file.Name] = $book
  } finally { $zip.Dispose() }
}

$fund = Canonicalize $all.Fund_Summary 'Fund_Summary'
$alloc = Canonicalize $all.Asset_Allocation 'Asset_Allocation'
$mgr = Canonicalize $all.Manager_Detail 'Manager_Detail'
$cash = Canonicalize $all.Cash_Flow_Detail 'Cash_Flow_Detail'
$bench = @($all.Benchmarks_Reference | Group-Object AssetClassLevel1, Benchmark | ForEach-Object {
  $_.Group | Sort-Object { $_._provenance.source_file } | Select-Object -Last 1
} | Sort-Object AssetClassLevel1)

$validations = @()
foreach ($r in $fund.rows) {
  $calcNet = [double]$r.Contributions_or_Gifts + [double]$r.BenefitPayments_or_Distributions + [double]$r.AdminFees + [double]$r.InvestmentManagementFees
  $calcEnd = [double]$r.BeginningMarketValue + [double]$r.NetCashFlow + [double]$r.InvestmentGainLoss
  $validations += [pscustomobject]@{
    type='fund_roll_forward'; fund=$r.FundCode; period=$r.Quarter; variance=[math]::Round($calcEnd - [double]$r.EndingMarketValue, 6)
    net_cash_flow_variance=[math]::Round($calcNet - [double]$r.NetCashFlow, 6); tolerance=0.05; status= if ([math]::Abs($calcEnd - [double]$r.EndingMarketValue) -le 0.05 -and [math]::Abs($calcNet - [double]$r.NetCashFlow) -le 0.05) {'pass'} else {'fail'}
  }
}
foreach ($g in ($alloc.rows | Group-Object FundCode, Quarter)) {
  $parts = $g.Name -split ', '
  $sumActual = ($g.Group | Measure-Object -Property PctOfFundTotal -Sum).Sum
  $sumMv = ($g.Group | Measure-Object -Property EndingMarketValue -Sum).Sum
  $fundRow = @($fund.rows | Where-Object { $_.FundCode -eq $parts[0] -and $_.Quarter -eq $parts[1] } | Select-Object -First 1)
  $mvVariance = if ($fundRow.Count) { [math]::Round($sumMv - [double]$fundRow[0].EndingMarketValue, 6) } else { $null }
  $validations += [pscustomobject]@{ type='allocation_total'; fund=$parts[0]; period=$parts[1]; allocation_total=[math]::Round($sumActual, 6); market_value_variance=$mvVariance; tolerance=0.05; status= if ([math]::Abs($sumActual - 100) -le 0.05 -and ($null -eq $mvVariance -or [math]::Abs($mvVariance) -le 0.05)) {'pass'} else {'fail'} }
}
foreach ($g in ($mgr.rows | Group-Object FundCode, Quarter, AssetClassLevel1)) {
  $first = @($g.Group)[0]
  $fundCode = $first.FundCode
  $quarter = $first.Quarter
  $assetClass = $first.AssetClassLevel1
  $sumMgr = ($g.Group | Measure-Object -Property MarketValue -Sum).Sum
  $assetRow = @($alloc.rows | Where-Object { $_.FundCode -eq $fundCode -and $_.Quarter -eq $quarter -and $_.AssetClassLevel1 -eq $assetClass } | Select-Object -First 1)
  $variance = if ($assetRow.Count) { [math]::Round($sumMgr - [double]$assetRow[0].EndingMarketValue, 6) } else { $null }
  $validations += [pscustomobject]@{ type='manager_rollup'; fund=$fundCode; period=$quarter; asset_class=$assetClass; variance=$variance; tolerance=0.05; status= if ($null -ne $variance -and [math]::Abs($variance) -le 0.05) {'pass'} else {'fail'} }
}
$assetClasses = @($alloc.rows | Select-Object -ExpandProperty AssetClassLevel1 -Unique | Sort-Object)
$managers = @($mgr.rows | Select-Object -ExpandProperty ManagerName -Unique | Sort-Object)
$funds = @($fund.rows | Select-Object FundCode, FundName, FundType -Unique | Sort-Object FundCode)
$benchCoverage = foreach ($a in $assetClasses) {
  [pscustomobject]@{ asset_class=$a; has_benchmark= @(($bench | Where-Object { $_.AssetClassLevel1 -eq $a })).Count -gt 0 }
}

$model = [ordered]@{
  generated_at = (Get-Date).ToString('s')
  methodology = [ordered]@{
    units = 'USD millions ($M); returns in percent; excess returns in basis points in source and percentage points in UI.'
    fytd = 'Each workbook is a fiscal-year-to-date snapshot through its file quarter; canonical quarter records are deduplicated by quarter-end date.'
    roll_forward = 'Ending Market Value = Beginning Market Value + Net Cash Flow + Investment Gain/Loss.'
    net_cash_flow = 'Contributions/Gifts + Benefit Payments/Distributions + Admin Fees + Investment Management Fees.'
    policy_benchmark = 'Fund policy benchmark return is target-weighted asset-class benchmark return.'
  }
  files = @($books.Values | Sort-Object file)
  dimensions = [ordered]@{ funds=$funds; periods=@('Q1','Q2','Q3','Q4','FY2026'); asset_classes=$assetClasses; managers=$managers; benchmark_coverage=$benchCoverage }
  records = [ordered]@{ fund_summary=$fund.rows; asset_allocation=$alloc.rows; manager_detail=$mgr.rows; cash_flow_detail=$cash.rows; benchmarks_reference=$bench; raw_export_sample=$all.RAW_Export_Extract }
  audit = [ordered]@{
    duplicate_records = [ordered]@{ fund_summary=$fund.duplicates; asset_allocation=$alloc.duplicates; manager_detail=$mgr.duplicates; cash_flow_detail=$cash.duplicates }
    validations = $validations
    raw_export_assessment = 'RAW_Export_Extract contains source-system metadata blocks, JSON config, blank spacer rows, cryptic platform column IDs and display-label rows before data. The structured sheets are safer for the primary reporting pipeline; raw parsing would require skipping metadata, selecting the technical header row, coercing types, and mapping platform IDs to reporting fields.'
  }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$json = $model | ConvertTo-Json -Depth 40
Set-Content -LiteralPath (Join-Path $OutDir 'beacon-data.json') -Value $json -Encoding UTF8
Set-Content -LiteralPath '.\tools\beacon-model-audit.json' -Value $json -Encoding UTF8
Write-Output "Wrote $(Join-Path $OutDir 'beacon-data.json')"
