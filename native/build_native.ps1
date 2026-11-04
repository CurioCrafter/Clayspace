param(
    [string]$LibraryDir = 'D:\BlenderRewrite\lib\windows_x64',
    [ValidateSet('bf_intern_ghost', 'bf_windowmanager', 'blender', 'install')][string]$Target = 'blender',
    [int]$Jobs = 4
)
$ErrorActionPreference = 'Stop'
$nativeRoot = $PSScriptRoot
$sourceRoot = Join-Path $nativeRoot 'blender-5.1.2'
$buildRoot = Join-Path $nativeRoot 'build-ninja'
$LibraryDir = (Resolve-Path -LiteralPath $LibraryDir).Path.Replace('\', '/')
$revision = git -C $sourceRoot rev-parse HEAD
if ($revision -ne 'ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832') {
    throw "Expected Blender v5.1.2; found $revision"
}
& 'C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\Launch-VsDevShell.ps1' -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
cmake -S $sourceRoot -B $buildRoot -G Ninja `
    -DCMAKE_MAKE_PROGRAM=C:/msys64/mingw64/bin/ninja.exe `
    -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl -DCMAKE_BUILD_TYPE=Release `
    "-DLIBDIR=$LibraryDir" -DWITH_XR_OPENXR=ON -DWITH_CYCLES=OFF `
    -DWITH_LIBMV=OFF -DWITH_OPENVDB=OFF -DWITH_USD=OFF `
    -DWITH_OPENIMAGEDENOISE=OFF -DWITH_LLVM=OFF -DWITH_CLANG=OFF `
    -DWITH_GTESTS=OFF -DWITH_PYTHON_INSTALL=ON -DWITH_VULKAN_BACKEND=OFF
if ($LASTEXITCODE -ne 0) { throw 'Native CMake configuration failed.' }
cmake --build $buildRoot --target $Target --parallel $Jobs 2>&1 |
    Tee-Object -FilePath (Join-Path $nativeRoot "build-$Target.log") -Append
if ($LASTEXITCODE -ne 0) { throw "Native $Target build failed; see build-$Target.log." }
