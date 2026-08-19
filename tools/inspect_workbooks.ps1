param(
  [string]$DataDir = ".\Data",
  [string]$OutFile = ".\tools\workbook_audit.json"
)

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-ZipEntryText {
  param($Zip, [string]$Name)
  $entry = $Zip.GetEntry($Name)
  if (-not $entry) { return $null }
  $reader = [System.IO.StreamReader]::new($entry.Open())
  try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Get-ColumnIndex {
  param([string]$CellRef)
  $letters = ($CellRef -replace '[0-9]', '').ToUpperInvariant()
  $n = 0
  foreach ($ch in $letters.ToCharArray()) {
    $n = ($n * 26) + ([int][char]$ch - [int][char]'A' + 1)
  }
  return $n
}

function Convert-CellValue {
  param($Cell, $SharedStrings)
  $type = $Cell.GetAttribute('t')
  $valueNode = $Cell.SelectSingleNode("*[local-name()='v']")
  if ($type -eq 'inlineStr') {
    $texts = @()
    foreach ($t in $Cell.SelectNodes(".//*[local-name()='t']")) { $texts += [string]$t.InnerText }
    return ($texts -join '')
  }
  if ($null -eq $valueNode) { return $null }
  $raw = [string]$valueNode.InnerText
  if ($type -eq 's') {
    $idx = 0
    if ([int]::TryParse($raw, [ref]$idx) -and $idx -lt $SharedStrings.Count) {
      return $SharedStrings[$idx]
    }
  }
  if ($type -eq 'b') { return $raw -eq '1' }
  $num = 0.0
  if ([double]::TryParse($raw, [Globalization.NumberStyles]::Any, [Globalization.CultureInfo]::InvariantCulture, [ref]$num)) {
    return $num
  }
  return $raw
}

function Get-SharedStrings {
  param($Zip)
  $xmlText = Get-ZipEntryText $Zip 'xl/sharedStrings.xml'
  $items = [System.Collections.Generic.List[string]]::new()
  if (-not $xmlText) { return $items }
  [xml]$xml = $xmlText
  foreach ($si in $xml.SelectNodes("//*[local-name()='si']")) {
    $parts = @()
    foreach ($t in $si.SelectNodes(".//*[local-name()='t']")) {
      $parts += [string]$t.InnerText
    }
    $items.Add(($parts -join '')) | Out-Null
  }
  return $items
}

function Get-SheetMap {
  param($Zip)
  [xml]$workbook = Get-ZipEntryText $Zip 'xl/workbook.xml'
  [xml]$rels = Get-ZipEntryText $Zip 'xl/_rels/workbook.xml.rels'
  $relMap = @{}
  foreach ($rel in $rels.SelectNodes("//*[local-name()='Relationship']")) {
    $target = [string]$rel.Target
    if ($target.StartsWith('/')) {
      $relMap[$rel.Id] = $target.TrimStart('/')
    } else {
      $relMap[$rel.Id] = 'xl/' + $target.TrimStart('/')
    }
  }
  $sheets = @()
  foreach ($sheet in $workbook.SelectNodes("//*[local-name()='sheet']")) {
    $rid = $sheet.GetAttribute('id', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')
    $sheets += [pscustomobject]@{
      name = [string]$sheet.name
      id = [string]$sheet.sheetId
      path = $relMap[$rid]
    }
  }
  return $sheets
}

function Read-SheetRows {
  param($Zip, [string]$Path, $SharedStrings, [int]$MaxRows = 2000)
  $xmlText = Get-ZipEntryText $Zip $Path
  [xml]$xml = $xmlText
  $rows = @()
  foreach ($row in $xml.SelectNodes("//*[local-name()='sheetData']/*[local-name()='row']")) {
    if ($rows.Count -ge $MaxRows) { break }
    $cells = @{}
    $maxCol = 0
    foreach ($c in $row.SelectNodes("*[local-name()='c']")) {
      $idx = Get-ColumnIndex ([string]$c.r)
      $maxCol = [Math]::Max($maxCol, $idx)
      $cells[$idx] = Convert-CellValue $c $SharedStrings
    }
    $values = @()
    for ($i = 1; $i -le $maxCol; $i++) { $values += $cells[$i] }
    $rows += ,$values
  }
  return $rows
}

$audit = @{}
foreach ($file in Get-ChildItem -LiteralPath $DataDir -Filter '*.xlsx' | Sort-Object Name) {
  $zip = [System.IO.Compression.ZipFile]::OpenRead($file.FullName)
  try {
    $shared = Get-SharedStrings $zip
    $sheetMap = Get-SheetMap $zip
    $book = [ordered]@{ sheets = [ordered]@{}; readme = @() }
    foreach ($sheet in $sheetMap) {
      $rows = Read-SheetRows $zip $sheet.path $shared
      $nonEmpty = @($rows | Where-Object { ($_ | Where-Object { $null -ne $_ -and "$_" -ne '' }).Count -gt 0 })
      $headerRow = $null
      $headers = @()
      for ($i = 0; $i -lt [Math]::Min($rows.Count, 25); $i++) {
        $vals = @($rows[$i] | Where-Object { $null -ne $_ -and "$_" -ne '' })
        if ($vals.Count -ge 2) {
          $headerRow = $i + 1
          $headers = $rows[$i]
          break
        }
      }
      $book.sheets[$sheet.name] = [ordered]@{
        rows = $rows.Count
        non_empty_rows = $nonEmpty.Count
        cols = (($rows | ForEach-Object { $_.Count } | Measure-Object -Maximum).Maximum)
        header_row = $headerRow
        header = $headers
        sample = @($rows | Select-Object -First 12)
      }
      if ($sheet.name -eq 'ReadMe') {
        $book.readme = @($nonEmpty | ForEach-Object { @($_ | Where-Object { $null -ne $_ -and "$_" -ne '' }) })
      }
    }
    $audit[$file.Name] = $book
  } finally {
    $zip.Dispose()
  }
}

$json = $audit | ConvertTo-Json -Depth 20
Set-Content -LiteralPath $OutFile -Value $json -Encoding UTF8
Write-Output $json
