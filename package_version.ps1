# Write the package version to the file named VERSION
# Package version number is determined by the latest git tag.
$tag = git describe --tags (git rev-list --tags --max-count=1);
$tag -match 'v(?<version>\d+\.\d+\.\d+).*';
Write-Output "Writing git tag based version to file VERSION:  $($Matches.version)"
$Matches.version | Out-File -FilePath VERSION -Encoding ASCII -NoNewline;