#!/bin/bash

echo "Downloading server binary..."
curl -o AmazonGameLiftSampleServerBinary.zip 'https://ws-assets-prod-iad-r-iad-ed304a55c2ca1aee.s3.us-east-1.amazonaws.com/086bb355-4fdc-4e63-8ca7-af7cfc45d4f2/AmazonGameLiftSampleServerBinary.zip'
# Extract to the LinuxServerBuild folder
unzip AmazonGameLiftSampleServerBinary.zip -d ../LinuxServerBuild
echo "Done!"
