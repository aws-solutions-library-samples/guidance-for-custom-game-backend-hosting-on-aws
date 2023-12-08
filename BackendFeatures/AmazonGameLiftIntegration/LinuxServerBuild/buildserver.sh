#!/bin/bash

echo "Building the server and copying output to LinuxServerBuild..."
docker build --output=../LinuxServerBuild --target=server .
echo "Done!"
