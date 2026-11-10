param(
    [string]$JavaDirectory='E:\create\focusguard\tools\jdk\jdk-17.0.20.1+1',
    [string]$BuildToolsDirectory='E:\create\focusguard\tools\build-tools-35.0.0\android-15',
    [string]$AndroidJar='E:\create\focusguard\tools\platforms-android-35\android-35-ext15\android.jar',
    [Parameter(Mandatory=$true)][string]$SigningKey,
    [string]$SigningAlias='si-preview',
    [ValidatePattern('^[A-Za-z0-9.-]+$')][string]$VersionName='0.1.3-preview',
    [ValidateRange(1,2147483647)][int]$VersionCode=1007,
    [switch]$Force
)
$ErrorActionPreference='Stop'
$project=$PSScriptRoot
if(-not (Test-Path -LiteralPath $SigningKey -PathType Leaf)) { throw "Signing key not found: $SigningKey" }
if(-not $env:SI_ANDROID_KEYSTORE_PASSWORD -or -not $env:SI_ANDROID_KEY_PASSWORD) {
    throw 'Set SI_ANDROID_KEYSTORE_PASSWORD and SI_ANDROID_KEY_PASSWORD in the local shell.'
}
if($VersionName -eq '.' -or $VersionName -eq '..') { throw "Invalid version name: $VersionName" }
function Run-Tool($Executable,$Arguments) {
    & $Executable @Arguments
    if($LASTEXITCODE -ne 0) { throw "Tool failed: $Executable ($LASTEXITCODE)" }
}
foreach($required in @("$JavaDirectory\bin\javac.exe","$BuildToolsDirectory\aapt2.exe",$AndroidJar,"$project\tools\NormalizeApkPaths.java")) {
    if(-not (Test-Path -LiteralPath $required)) { throw "Missing build dependency: $required" }
}
$build=Join-Path $project "build\$VersionName"
$apk=Join-Path $project "dist\SiCompanion-Android-$VersionName.apk"
if((Test-Path -LiteralPath $apk) -and -not $Force) {
    throw "Target APK already exists: $apk. Use a new VersionName, or pass -Force to replace this target only."
}
New-Item -ItemType Directory -Force -Path $build,"$build\classes","$build\generated","$build\dex","$project\dist" | Out-Null
Run-Tool "$BuildToolsDirectory\aapt2.exe" @('compile','--dir',"$project\app\src\main\res",'-o',"$build\resources.zip")
Run-Tool "$BuildToolsDirectory\aapt2.exe" @('link','-o',"$build\unsigned.apk",'-I',$AndroidJar,'--manifest',"$project\app\src\main\AndroidManifest.xml",'--java',"$build\generated",'-A',"$project\app\src\main\assets",'--min-sdk-version','26','--target-sdk-version','35','--version-code',"$VersionCode",'--version-name',$VersionName,"$build\resources.zip")
$sources=@(Get-ChildItem "$project\app\src\main\java","$build\generated" -Recurse -Filter *.java | ForEach-Object FullName)
Run-Tool "$JavaDirectory\bin\javac.exe" (@('-encoding','UTF-8','--release','8','-classpath',$AndroidJar,'-d',"$build\classes")+$sources)
$classes=@(Get-ChildItem "$build\classes" -Recurse -Filter *.class | ForEach-Object FullName)
Run-Tool "$JavaDirectory\bin\java.exe" (@('-cp',"$BuildToolsDirectory\lib\d8.jar",'com.android.tools.r8.D8','--min-api','26','--lib',$AndroidJar,'--output',"$build\dex")+$classes)
Run-Tool "$JavaDirectory\bin\jar.exe" @('--update','--file',"$build\unsigned.apk",'-C',"$build\dex",'classes.dex')
$normalized=Join-Path $build "normalized.apk"
Run-Tool "$JavaDirectory\bin\java.exe" @("$project\tools\NormalizeApkPaths.java","$build\unsigned.apk",$normalized)
Run-Tool "$BuildToolsDirectory\zipalign.exe" @('-f','4',$normalized,"$build\aligned.apk")
Run-Tool "$JavaDirectory\bin\java.exe" @('-jar',"$BuildToolsDirectory\lib\apksigner.jar",'sign','--ks',$SigningKey,'--ks-key-alias',$SigningAlias,'--ks-pass','env:SI_ANDROID_KEYSTORE_PASSWORD','--key-pass','env:SI_ANDROID_KEY_PASSWORD','--out',$apk,"$build\aligned.apk")
Run-Tool "$JavaDirectory\bin\java.exe" @('-jar',"$BuildToolsDirectory\lib\apksigner.jar",'verify','--verbose',$apk)
Run-Tool "$BuildToolsDirectory\aapt2.exe" @('dump','badging',$apk)
Get-FileHash -LiteralPath $apk -Algorithm SHA256 | Format-List
