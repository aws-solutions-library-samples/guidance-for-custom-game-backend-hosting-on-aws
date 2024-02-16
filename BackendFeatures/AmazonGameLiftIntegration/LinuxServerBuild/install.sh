#!/bin/bash

# Set binary execution permissions NOTE: Change this if you have a different binary name
sudo chmod 777 /local/game/GameLiftSampleServer

# Download and install the agent
wget https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
sudo rpm -U ./amazon-cloudwatch-agent.rpm

# Copy the cloudwatch agent configuration file to the right directory
sudo cp amazon-cloudwatch-agent.json /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# Copy the common-config.toml to the right directory to set shared credentials access with the Fleet role
sudo cp common-config.toml /opt/aws/amazon-cloudwatch-agent/etc/common-config.toml

# Start the agent
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

