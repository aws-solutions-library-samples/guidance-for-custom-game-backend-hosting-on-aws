import json
import pathlib
import aws_cdk as cdk

from aws_cdk import (
    aws_s3 as _s3,
    aws_s3_deployment as _deployment,
    aws_kinesis as _kds,
    aws_iam as _iam,
    aws_glue as _glue,
    aws_apigatewayv2 as _api,
    aws_logs as _logs,
    aws_lambda as _lambda
)
from constructs import Construct

class DeltaLakeIntegrationBackend(cdk.Stack):

    def __init__(self, scope: Construct, construct_id: str, *, endpoint_url: str, etl_script: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create an S3 Bucket to serve as the datalake object store
        s3_bucket = _s3.Bucket(
            self,
            "DeltaLakeBucket",
            # bucket_name=f"{workload.lower()}-{cdk.Aws.REGION}-{cdk.Aws.ACCOUNT_ID}",
            versioned=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        # cdk.CfnOutput(self, "DataLakeBucketName", value=s3_bucket.bucket_name)

        # Deploy SDK asset `aws-sdk-java-2.17.224.jar` deployment for Glue Streaming job
        _deployment.BucketDeployment(
            self,
            "AssetsDeployment",
            sources=[
                _deployment.Source.asset(
                    path=str(pathlib.Path(__file__).parent.parent.joinpath("assets").resolve())
                )
            ],
            destination_bucket=s3_bucket,
            destination_key_prefix="assets"
        )

        # Deploy script assets for Glue Streaming job
        _deployment.BucketDeployment(
            self,
            "ScriptsDeployment",
            sources=[
                _deployment.Source.asset(
                    path=str(pathlib.Path(__file__).parent.parent.joinpath("scripts").resolve())
                )
            ],
            destination_bucket=s3_bucket,
            destination_key_prefix="scripts"
        )

        # Create the Lambda logging shared policy
        lambda_basics_policy = _iam.PolicyStatement(
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            effect=_iam.Effect.ALLOW,
            resources=["*"]
        )

        # Create the shared Lambda execution policy
        lambda_logging_policy = _iam.PolicyStatement(
            actions=[
                "logs:DeleteRetentionPolicy",
                "logs:PutRetentionPolicy"
            ],
            effect=_iam.Effect.ALLOW,
            resources=["*"]
        )

        # Create a shared IAM Role for for Lambda execution and logging
        logging_role = _iam.Role(
            self,
            "LambdaLoggingRole",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "LambdaLoggingPolicy": _iam.PolicyDocument(
                    statements=[
                        lambda_logging_policy
                    ]
                ),
                "LambdaBasicPolicy": _iam.PolicyDocument(
                    statements=[
                        lambda_basics_policy
                    ]
                )
            }
        )

        # Create specific IAM Role for the `record` Lambda Function
        record_handler_role = _iam.Role(
            self,
            "RecordFunctionRole",
            assumed_by=_iam.ServicePrincipal("lambda.amazonaws.com")
        )
        record_handler_role.add_to_policy(lambda_basics_policy)

        # Create the Glue Job IAM Role
        glue_role = _iam.Role(
            self,
            "GlueJobRole",
            role_name="DeltalakeGlueRole",
            assumed_by=_iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                _iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSGlueServiceRole"),
                _iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMReadOnlyAccess"),
                _iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                _iam.ManagedPolicy.from_aws_managed_policy_name("AWSGlueConsoleFullAccess"),
                _iam.ManagedPolicy.from_aws_managed_policy_name("AmazonKinesisReadOnlyAccess")
            ],
            inline_policies={
                "S3Access": _iam.PolicyDocument(
                    statements=[
                        _iam.PolicyStatement(
                            actions=[
                                "s3:GetBucketLocation",
                                "s3:ListBucket",
                                "s3:GetBucketAcl",
                                "s3:GetObject",
                                "s3:PutObject",
                                "s3:DeleteObject"
                            ],
                            effect=_iam.Effect.ALLOW,
                            resources=[
                                f"{s3_bucket.bucket_arn}",
                                f"{s3_bucket.bucket_arn}/*"
                            ]
                        )
                    ]
                ),
                "PassRole": _iam.PolicyDocument(
                    statements=[
                        _iam.PolicyStatement(
                            actions=[
                                "iam:PassRole"
                            ],
                            effect=_iam.Effect.ALLOW,
                            resources=[
                                self.format_arn(
                                    service="iam",
                                    region="",
                                    resource="role",
                                    resource_name="DeltalakeGlueRole"
                                )
                            ]
                        )
                    ]
                )
            }
        )
        # cdk.CfnOutput(self, "GlueJobRoleName", value=glue_role.role_name)
        # cdk.CfnOutput(self, "GlueJobRoleArn", value=glue_role.role_arn)

        # Create the Kinesis Data Stream for data ingest
        stream = _kds.Stream(
            self,
            "IngestStream",
            stream_name="DataIngestStream",
            stream_mode=_kds.StreamMode.ON_DEMAND,
            retention_period=cdk.Duration.days(1)
        )
        stream.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        # cdk.CfnOutput(self, "DataIngestStreamName", value=stream.stream_name)

        # Create a Glue Catalog to store the stream data table
        stream_db_name = "deltalake_stream_db" # Must have underscores for SQL statements
        stream_db = _glue.CfnDatabase(
            self,
            "IngestStreamDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=_glue.CfnDatabase.DatabaseInputProperty(
                name=stream_db_name
            )
        )
        stream_db.apply_removal_policy(cdk.RemovalPolicy.DESTROY)

        # Create the stream data Glue Table
        stream_table_name = "kinesis_stream_table" # Must have underscores for SQL statements
        stream_table = _glue.CfnTable(
            self,
            "IngestStreamTable",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_name=stream_db_name,
            table_input=_glue.CfnTable.TableInputProperty(
                name=stream_table_name,
                parameters={"classification": "json"}, # Switching to JSON instead for parquet (GAP v2)
                table_type="EXTERNAL_TABLE",
                # TODO: Determine DeltaLake queries keys. This impacts `partition_keys` and `storage_descriptor`.
                #       Partition Keys is dependent on Parquet data, or custom event data to search from.
                # partition_keys=[
                #     _glue.CfnTable.ColumnProperty(
                #         name="year",
                #         type="string"
                #     ),
                #     _glue.CfnTable.ColumnProperty(
                #         name="month",
                #         type="string"
                #     ),
                #     _glue.CfnTable.ColumnProperty(
                #         name="day",
                #         type="string"
                #     )
                # ],
                storage_descriptor=_glue.CfnTable.StorageDescriptorProperty(
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    # TODO: Verify columns for game.
                    #       Using GAP V2 columns for interim testing
                    columns=[
                        _glue.CfnTable.ColumnProperty(
                            name="event_id",
                            type="string"
                        ),
                        _glue.CfnTable.ColumnProperty(
                            name="event_type",
                            type="string"
                        ),
                        _glue.CfnTable.ColumnProperty(
                            name="updated_at",
                            type="string"
                        ),
                        _glue.CfnTable.ColumnProperty(
                            name="event_data",
                            type="string"
                        )
                    ],
                    location=stream.stream_name,
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    parameters={
                        "streamARN": stream.stream_arn,
                        "typOfData": "kinesis"
                    },
                    serde_info=_glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe"
                    )
                )
            )
        )
        stream_table.add_dependency(stream_db)
        stream_table.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        # cdk.CfnOutput(self, "IngestStreamDatabaseName", value=stream_table.database_name)

        # Create a Glue Catalog for the data lake
        lake_db_name = "delta_lake_db" # Must have underscores for SQL statements
        lake_db = _glue.CfnDatabase(
            self,
            "DeltalakeDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=_glue.CfnDatabase.DatabaseInputProperty(
                name=lake_db_name,
                location_uri=s3_bucket.s3_url_for_object(key=f"{lake_db_name}/events")
            )
        )
        lake_db.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        # cdk.CfnOutput(self, "DeltalakeDatabaseName", value=lake_db.database_input.name)
        # cdk.CfnOutput(self, "DeltalakeDatabaseLocation", value=lake_db.database_input.location_uri)

        # Create the Delta Lake connection for Glue
        _glue.CfnConnection(
            self,
            "GlueDeltaLakeConnection",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            connection_input=_glue.CfnConnection.ConnectionInputProperty(
                name="deltalake-connector-1_0_0",
                description="Delta Lake Connector 1.0.0 for AWS Glue 3.0",
                connection_type="MARKETPLACE",
                connection_properties={
                    "CONNECTOR_TYPE": "Spark",
                    "CONNECTOR_URL": "https://709825985650.dkr.ecr.us-east-1.amazonaws.com/amazon-web-services/glue/delta:1.0.0-glue3.0-2",
                    "CONNECTOR_CLASS_NAME": "org.apache.spark.sql.delta.sources.DeltaDataSource"
                }
            )
        )

        # Create the Glue Stream ETL job
        glue_job_name = "GlueStreamEtlJob"
        _glue.CfnJob(
            self,
            "GlueETLJob",
            name=glue_job_name,
            description="AWS Glue Job to load the data from Kinesis Data Streams to Deltalake table in S3.",
            command=_glue.CfnJob.JobCommandProperty(
                name="gluestreaming",
                python_version="3",
                script_location=f"{s3_bucket.s3_url_for_object(key='scripts/') + etl_script}"
            ),
            role=glue_role.role_arn,
            connections=_glue.CfnJob.ConnectionsListProperty(
                connections=["deltalake-connector-1_0_0"] # Naming property for Delta Lake integration
            ),
            # TODO: `default_arguments` are specific to the `spark_datalake_writes` script.
            #        Explore how script updates + argument updates can be made through DataOps
            default_arguments={
                "--catalog": "spark_catalog", # Delta Lake GDC Demo sql catalog 
                "--database_name": lake_db_name,
                "--table_name": "events", # Game telemetry events for query
                "--primary_key": "event_id",
                "--partition_key": "event_type",
                "--kinesis_database_name": stream_db_name,
                "--kinesis_table_name": stream_table_name,
                "--kinesis_stream_arn": stream.stream_arn,
                "--starting_position_of_kinesis_iterator": "LATEST",
                "--delta_s3_path": s3_bucket.s3_url_for_object(key=f"{lake_db_name}/events"),
                "--aws_region": cdk.Aws.REGION,
                "--window_size": "100 seconds",
                "--extra-jars": s3_bucket.s3_url_for_object(key="assets/aws-sdk-java-2.23.13.jar"),
                "--extra-jars-first": "true",
                "--enable-metrics": "true",
                "--enable-spark-ui": "true",
                "--spark-event-logs-path": s3_bucket.s3_url_for_object(key=f"{lake_db_name}/events/spark_history_logs/"),
                "--enable-job-insights": "false",
                "--enable-glue-datacatalog": "true",
                "--enable-continuous-cloudwatch-log": "true",
                "--job-bookmark-option": "job-bookmark-disable",
                "--job-language": "python",
                "--TempDir": s3_bucket.s3_url_for_object(key=f"{lake_db_name}/events/temporary")
            },
            execution_property=_glue.CfnJob.ExecutionPropertyProperty(
                max_concurrent_runs=1
            ),
            glue_version="3.0",
            max_retries=0,
            timeout=2880,
            worker_type="G.1X",
            number_of_workers=2
        )
        # cdk.CfnOutput(self, "GlueJobName", value=glue_etl_job.name)
        # cdk.CfnOutput(self, "GlueJobRoleArn", value=glue_role.role_arn)

        # Create the HTTP API for data ingestion from the game client
        api = _api.CfnApi(
            self,
            "IngestionApi",
            name="DataIngestionHttpApi",
            description="Python Serverless HTTP API for Data Ingestion",
            protocol_type="HTTP"
        )

        # Create the CloudWatch Log Group for the HTTP API logs
        endpoint_logs = _logs.LogGroup(
            self,
            "IngestionApiLogs",
            retention=_logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY
        )

        # Define the API auto deployment stage
        _api.CfnStage(
            self,
            "IngestionApiStage",
            api_id=api.ref,
            stage_name="prod",
            auto_deploy=True,
            access_log_settings=_api.CfnStage.AccessLogSettingsProperty(
                destination_arn=endpoint_logs.log_group_arn,
                format="$context.requestId $context.requestTime $context.resourcePath $context.httpMethod $context.status $context.protocol"
            )
        )

        # Declare the Authorizer for the custom identity solution
        authorizer = _api.CfnAuthorizer(
            self,
            "BackendAuthorizer",
            api_id=api.ref,
            name="BackendAuthorizer",
            authorizer_type="JWT",
            identity_source=[
                "$request.header.Authorization"
            ],
            jwt_configuration=_api.CfnAuthorizer.JWTConfigurationProperty(
                audience=[
                    "gamebackend"
                ],
                issuer=endpoint_url
            )
        )

        # Create the `put_record` function to handle multiple event records into the Kinesis Stream
        record_handler = _lambda.Function(
            self,
            "RecordHandler",
            role=record_handler_role,
            code=_lambda.Code.from_asset(
                path="lambda",
                bundling=cdk.BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash", "-c", "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ]
                )
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.lambda_handler",
            timeout=cdk.Duration.seconds(30),
            tracing=_lambda.Tracing.ACTIVE,
            memory_size=1024,
            log_retention=_logs.RetentionDays.ONE_MONTH,
            log_retention_role=logging_role,
            environment={
                "STREAM_NAME": stream.stream_name,
            }
        )
        record_handler.add_permission(
            "InvokeRecordHandler",
            principal=_iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_account=cdk.Aws.ACCOUNT_ID,
            source_arn=f"arn:aws:execute-api:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:{api.ref}/prod/*",
            action="lambda:InvokeFunction"
        )
        stream.grant_write(record_handler)

        # Define the integration for the `put_record` function
        integration = _api.CfnIntegration(
            self,
            "RecordHandlerIntegration",
            api_id=api.ref,
            integration_type="AWS_PROXY",
            integration_uri=record_handler.function_arn,
            integration_method="POST",
            payload_format_version="2.0"
        )
        _api.CfnRoute(
            self,
            "PutRecordRoute",
            api_id=api.ref,
            route_key="POST /put-record",
            authorization_type="JWT",
            authorizer_id=authorizer.ref,
            target=f"integrations/{integration.ref}",
            authorization_scopes=["guest", "authenticated"]
        )

        # Endpoint URL
        cdk.CfnOutput(self, "IngestionApiURL", value=f"{api.attr_api_endpoint}/prod/put-record")