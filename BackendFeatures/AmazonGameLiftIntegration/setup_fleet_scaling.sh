#!/bin/bash

# Region definitions, make sure these match yours
region1="us-east-1" # We expect this to be your home region as well!
region2="us-west-2"
region3="eu-west-1"

# scaling config for all locations, adjust to your needs
minsize=1
maxsize=3
desired=1
availablesessions=30 # percentage of available sessions for scaling

# get the fleet ID from CloudFormation Export
FLEET_ID=$(aws cloudformation list-exports --query "Exports[?Name=='SampleGameLiftFleetID'].Value" --output text)
echo "Fleet ID: $FLEET_ID"

# Set fleet scaling limits
echo "Updating the fleet scaling configuration..."
aws gamelift update-fleet-capacity --fleet-id $FLEET_ID --min-size $minsize --max-size $maxsize --desired-instances $desired --location $region1 --region $region1
aws gamelift update-fleet-capacity --fleet-id $FLEET_ID --min-size $minsize --max-size $maxsize --desired-instances $desired --location $region2 --region $region1
aws gamelift update-fleet-capacity --fleet-id $FLEET_ID --min-size $minsize --max-size $maxsize --desired-instances $desired --location $region3 --region $region1

# Set fleet scaling policy
echo "Setting the fleet scaling policy..."
aws gamelift put-scaling-policy --name ExampleFleetScaling --fleet-id $FLEET_ID --policy-type TargetBased --target-configuration TargetValue=$availablesessions --metric-name PercentAvailableGameSessions --region $region1
echo 'Done'