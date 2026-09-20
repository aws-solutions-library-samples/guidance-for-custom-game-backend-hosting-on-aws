Write-Host "Downloading server binary..."
Invoke-WebRequest -Uri 'https://ws-assets-prod-iad-r-iad-ed304a55c2ca1aee.s3.us-east-1.amazonaws.com/086bb355-4fdc-4e63-8ca7-af7cfc45d4f2/AmazonGameLiftSampleServerBinary.zip' -OutFile AmazonGameLiftSampleServerBinary.zip

# Extract to the LinuxServerBuild folder  
Expand-Archive -Path AmazonGameLiftSampleServerBinary.zip -DestinationPath ../LinuxServerBuild

Write-Host "Done!"
