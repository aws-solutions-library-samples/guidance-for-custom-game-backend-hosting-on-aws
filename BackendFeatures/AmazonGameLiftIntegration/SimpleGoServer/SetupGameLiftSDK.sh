#!/bin/bash

# Install all required packages
echo "Installing required packages, this will take some time... "
sudo yum install -y golang

# Download the GameLift SDK, NOTE: You can replace this with the latest version
if [ ! -d "..GameLift-Go-ServerSDK-5.0.0" ]; then
    # script statements if $DIR doesn't exist.
    echo "Download and unzip GameLift Server SDK"
    cd ..
    wget https://gamelift-server-sdk-release.s3.us-west-2.amazonaws.com/go/GameLift-Go-ServerSDK-5.0.0.zip
    unzip GameLift-Go-ServerSDK-5.0.0.zip
    rm GameLift-Go-ServerSDK-5.0.0.zip
else
    echo "GameLift Server SDK already downloaded."
fi
