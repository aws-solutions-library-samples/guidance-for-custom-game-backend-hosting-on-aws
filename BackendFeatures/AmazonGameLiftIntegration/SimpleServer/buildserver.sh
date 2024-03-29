#!/bin/bash

echo "Building the server and copying output to LinuxServerBuild..."
docker buildx build --platform=linux/amd64 --output=../LinuxServerBuild --target=server .
rm ../LinuxServerBuild/*.cpp 
rm ../LinuxServerBuild/*.h
echo "Done!"
