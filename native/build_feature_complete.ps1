param(
    [ValidateSet('blender', 'install')][string]$Target = 'install',
    [ValidateRange(1, 16)][int]$Jobs = 4,
    [switch]$ConfigureOnly,
    [string]$VisualStudioPath,
    [string]$NinjaExe = 'ninja.exe'
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Join-Path $PSScriptRoot 'blender-5.1.2'
$libraryRoot = Join-Path $PSScriptRoot 'dependencies\windows_x64_5_1'
$buildRoot = Join-Path $PSScriptRoot 'build-features'
$installRoot = Join-Path $buildRoot 'bin'
if ((git -C $sourceRoot rev-parse HEAD) -ne 'ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832') {
    throw 'The feature build requires the pinned Blender 5.1.2 source.'
}
if ((git -C $libraryRoot rev-parse HEAD) -ne '17e87e0869383bcf1dd8339502e6008b7c0c8eb0') {
    throw 'The feature build requires the matching official Windows libraries.'
}
$libraryFiles = @(git -C $libraryRoot lfs ls-files)
if ($LASTEXITCODE -ne 0 -or !$libraryFiles.Count -or ($libraryFiles | Where-Object { $_ -match '^\w+ - ' })) {
    throw 'Official library downloads are incomplete. Finish git lfs pull in the dependency directory.'
}
if (!$VisualStudioPath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (!(Test-Path -LiteralPath $vswhere)) {
        throw 'Visual Studio discovery is unavailable. Install VS 2022 C++ tools or pass -VisualStudioPath.'
    }
    $VisualStudioPath = & $vswhere -latest -products '*' -version '[17.0,18.0)' `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or !$VisualStudioPath) {
        throw 'Visual Studio 2022 with x64 C++ build tools was not found.'
    }
}
$devShell = Join-Path $VisualStudioPath 'Common7\Tools\Launch-VsDevShell.ps1'
if (!(Test-Path -LiteralPath $devShell -PathType Leaf)) {
    throw 'Launch-VsDevShell.ps1 was not found under -VisualStudioPath.'
}
& $devShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
$ninja = Get-Command $NinjaExe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (!$ninja -and $NinjaExe -eq 'ninja.exe' -and (Test-Path -LiteralPath 'C:\msys64\mingw64\bin\ninja.exe')) {
    $ninja = Get-Command 'C:\msys64\mingw64\bin\ninja.exe' -CommandType Application
}
if (!$ninja) { throw 'Ninja was not found. Add it to PATH or pass -NinjaExe with its executable path.' }
$linker = Join-Path (Split-Path (Get-Command cl.exe -CommandType Application).Source) 'link.exe'
$archiver = Join-Path (Split-Path (Get-Command cl.exe -CommandType Application).Source) 'lib.exe'
if (!(Test-Path -LiteralPath $linker) -or !(Test-Path -LiteralPath $archiver)) {
    throw 'Visual Studio linker or archiver was not found next to cl.exe.'
}
# Use the upstream release feature set in a separate build directory. GPU
# kernel bundles require separate vendor SDK setup; GPU Cycles needs its own
# build and hardware test before production parity can be claimed.
cmake -S $sourceRoot -B $buildRoot -G Ninja `
    -C (Join-Path $sourceRoot 'build_files\cmake\config\blender_release.cmake') `
    "-DCMAKE_MAKE_PROGRAM=$($ninja.Source.Replace('\', '/'))" `
    "-DCMAKE_INSTALL_PREFIX=$($installRoot.Replace('\', '/'))" `
    -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl "-DCMAKE_LINKER=$linker" "-DCMAKE_AR=$archiver" -DCMAKE_BUILD_TYPE=Release `
    "-DLIBDIR=$($libraryRoot.Replace('\', '/'))" `
    -DWITH_CYCLES_CUDA_BINARIES=OFF -DWITH_CYCLES_HIP_BINARIES=OFF `
    -DWITH_CYCLES_ONEAPI_BINARIES=OFF -DWITH_CYCLES_DEVICE_OPTIX=OFF `
    -DWITH_CYCLES_DEVICE_HIPRT=OFF -DWITH_XR_OPENXR=ON -DWITH_VULKAN_BACKEND=ON -DWITH_GTESTS=OFF
if ($LASTEXITCODE -ne 0) { throw 'Feature-build configuration failed.' }
if ($ConfigureOnly) { return }
$buildLog = Join-Path $PSScriptRoot ("build-features-{0}-{1}.log" -f $Target, (Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Output "Build log: $buildLog"
cmake --build $buildRoot --target $Target --parallel $Jobs 2>&1 |
    Tee-Object -FilePath $buildLog
if ($LASTEXITCODE -ne 0) { throw 'Feature build failed; see its project log.' }
