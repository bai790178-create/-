$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv314\Scripts\python.exe"
$DistRoot = Join-Path $ProjectRoot "dist"
$DriveRoot = (Split-Path -Qualifier ([System.IO.Path]::GetFullPath($ProjectRoot))) + "\"
$PackageDirName = -join ([char[]](0x6253, 0x5305, 0x6587, 0x4EF6))
$PackageRoot = Join-Path $DriveRoot $PackageDirName
$ClientName = "UltrasonicGratingClient"
$ClientDir = Join-Path $DistRoot $ClientName
$ClientExe = Join-Path $PackageRoot "$ClientName.exe"
$BuildRoot = Join-Path $DriveRoot "ug_client_build"
$BuildVenv = Join-Path $DriveRoot "ug_client_venv"
$ProjectSdkDir = Join-Path $ProjectRoot "sdk"
$ExistingClientSdkDir = Join-Path $ClientDir "sdk"
$SdkSourceCandidates = @()
if ($env:CK_CAMERA_SDK_DIR) {
  $SdkSourceCandidates += $env:CK_CAMERA_SDK_DIR
}
$SdkSourceCandidates += $ProjectSdkDir
if (Test-Path -LiteralPath (Join-Path $ExistingClientSdkDir "CKCameraDLL_X64.dll")) {
  $SdkSourceCandidates += $ExistingClientSdkDir
}
$DriveSdkDir = Get-ChildItem -Path $DriveRoot -Directory -ErrorAction SilentlyContinue |
  Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "CKCameraDLL_X64.dll") } |
  Select-Object -First 1 -ExpandProperty FullName
if ($DriveSdkDir) {
  $SdkSourceCandidates += $DriveSdkDir
}
$SdkSourceDir = $null
foreach ($Candidate in $SdkSourceCandidates) {
  if ($Candidate -and (Test-Path -LiteralPath (Join-Path $Candidate "CKCameraDLL_X64.dll"))) {
    $SdkSourceDir = $Candidate
    break
  }
}

if (-not (Test-Path -LiteralPath $Python)) {
  Write-Host "Project Python environment not found. Please run .\setup_env.ps1 first." -ForegroundColor Yellow
  exit 1
}

if (-not (Test-Path -LiteralPath $PackageRoot)) {
  New-Item -ItemType Directory -Path $PackageRoot | Out-Null
}

$ResolvedBuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$ResolvedBuildVenv = [System.IO.Path]::GetFullPath($BuildVenv)
if (-not $ResolvedBuildRoot.EndsWith("\ug_client_build") -or -not $ResolvedBuildVenv.EndsWith("\ug_client_venv")) {
  throw "Refusing to clean unexpected build paths."
}

if (Test-Path -LiteralPath $BuildVenv) {
  Remove-Item -LiteralPath $BuildVenv -Recurse -Force
}
& $Python -m venv $BuildVenv
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
& $BuildPython -m pip install --upgrade pip
& $BuildPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
& $BuildPython -m pip install pyinstaller

if (Test-Path -LiteralPath $BuildRoot) {
  Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $BuildRoot | Out-Null

$ItemsToCopy = @("main.py", "src", "assets")
foreach ($Item in $ItemsToCopy) {
  Copy-Item -LiteralPath (Join-Path $ProjectRoot $Item) -Destination $BuildRoot -Recurse
}

if ($SdkSourceDir) {
  $BuildSdkDir = Join-Path $BuildRoot "sdk"
  New-Item -ItemType Directory -Path $BuildSdkDir | Out-Null

  $SdkDllPatterns = @(
    "CKCameraDLL_X64.dll",
    "hAcqCKCamera*_X64.dll",
    "imaqCKCamera*_X64.dll",
    "TomasCamera.dll",
    "TomasCKcamera.dll",
    "TomasCommandLineTools.dll",
    "TomasImage.dll",
    "TomasVideoAnalysis.dll"
  )
  $CopiedSdkFiles = @{}
  foreach ($Pattern in $SdkDllPatterns) {
    Get-ChildItem -Path $SdkSourceDir -File -Filter $Pattern -ErrorAction SilentlyContinue | ForEach-Object {
      if (-not $CopiedSdkFiles.ContainsKey($_.Name)) {
        Copy-Item -LiteralPath $_.FullName -Destination $BuildSdkDir
        $CopiedSdkFiles[$_.Name] = $true
      }
    }
  }

  if (-not (Test-Path -LiteralPath (Join-Path $BuildSdkDir "CKCameraDLL_X64.dll"))) {
    Write-Warning "CK camera SDK source was found, but CKCameraDLL_X64.dll was not copied."
  } else {
    Write-Host "CK camera SDK staged: $BuildSdkDir" -ForegroundColor Green
  }
} else {
  Write-Warning "CK camera SDK not found. Set CK_CAMERA_SDK_DIR or put SDK DLLs in $ProjectSdkDir before building."
}

if (Test-Path -LiteralPath $ClientExe) {
  Remove-Item -LiteralPath $ClientExe -Force
}
Get-ChildItem -LiteralPath $PackageRoot -Force | Where-Object { $_.FullName -ne $ClientExe } | ForEach-Object {
  Remove-Item -LiteralPath $_.FullName -Recurse -Force
}

Push-Location $BuildRoot
try {
  $PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onefile",
    "--name", $ClientName,
    "--distpath", $PackageRoot,
    "--workpath", (Join-Path $BuildRoot "build"),
    "--specpath", $BuildRoot,
    "--paths", (Join-Path $BuildRoot "src"),
    "--add-data", "src;src",
    "--add-data", "assets;assets",
    "--hidden-import", "cv2",
    "--hidden-import", "PIL.Image",
    "--hidden-import", "numpy"
  )
  if ($SdkSourceDir) {
    $PyInstallerArgs += @("--add-data", "sdk;sdk")
  }
  $PyInstallerArgs += "main.py"

  & $BuildPython -m PyInstaller @PyInstallerArgs

  if (-not (Test-Path -LiteralPath $ClientExe)) {
    throw "PyInstaller output not found: $ClientExe"
  }

  Write-Host "Client generated: $ClientExe" -ForegroundColor Green
  Write-Host "Copy this .exe to another Windows PC to run it." -ForegroundColor Green
}
finally {
  Pop-Location
}

if (Test-Path -LiteralPath $BuildRoot) {
  Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
if (Test-Path -LiteralPath $BuildVenv) {
  Remove-Item -LiteralPath $BuildVenv -Recurse -Force
}
