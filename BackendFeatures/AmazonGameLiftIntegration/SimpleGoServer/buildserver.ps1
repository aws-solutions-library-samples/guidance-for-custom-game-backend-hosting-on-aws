Write-Output "Building the server and copying output to LinuxServerBuild..."

docker-buildx build --platform=linux/amd64 --output=..\LinuxServerBuild --target=server .

Write-Output "Done!"
