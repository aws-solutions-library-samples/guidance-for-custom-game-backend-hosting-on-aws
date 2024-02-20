#!/usr/bin/env python3
import boto3
import aws_cdk as cdk

from delta_lake_integration.integration_backend_stack import DeltaLakeIntegrationBackend

# Global variables
ISSUER_ENDPOINT = "https://d3idu5tcczu4yd.cloudfront.net"
AWS_ACCOUNT = boto3.client("sts").get_caller_identity()["Account"]
AWS_REGION= "us-east-1"
ETL_SCRIPT = "spark_datalake_writes.py"

app = cdk.App()
backend = DeltaLakeIntegrationBackend(
    app,
    "DeltaLakeIntegrationBackend",
    env=cdk.Environment(
        account=AWS_ACCOUNT,
        region=AWS_REGION
    ),
    issuer_endpoint_url=ISSUER_ENDPOINT,
    etl_script_name=ETL_SCRIPT
)

app.synth()
