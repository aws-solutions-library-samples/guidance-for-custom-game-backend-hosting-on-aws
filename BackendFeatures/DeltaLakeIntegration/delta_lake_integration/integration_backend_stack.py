import pathlib
import aws_cdk as cdk

from aws_cdk import (
    aws_s3 as _s3,
    aws_s3_deployment as _deployment,
    aws_kinesis as _kinesis,
    aws_glue as _glue,
    aws_iam as _iam,
    aws_apigatewayv2 as _api,
    aws_logs as _logs
)
from constructs import Construct

class DeltaLakeIntegrationBackend(cdk.Stack):

    def __init__(self, scope: Construct, construct_id: str, *, issuer_endpoint_url: str, etl_script_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create an S3 Bucket to serve as the datalake object store
        s3_bucket = _s3.Bucket(
            self,
            "DeltalakeBucket",
            bucket_name=f"DeltalakeBucket-{cdk.Aws.REGION}-{cdk.Aws.ACCOUNT_ID}",
            versioned=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        # cdk.CfnOutput(self, "DeltalakeBucketName", value=s3_bucket.bucket_name)

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

        # Create the Kinesis Data Stream for data ingest
        stream = _kinesis.Stream(
            self,
            "IngestStream",
            stream_name="DataIngestStream",
            stream_mode=_kinesis.StreamMode.ON_DEMAND,
            retention_period=cdk.Duration.days(1)
        )
        stream.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        # cdk.CfnOutput(self, "DataIngestStreamName", value=stream.stream_name)

        # Create a Glue Catalog to store the stream data table
        stream_db_name = "IngestStreamDB"
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
        stream_table_name = "DataIngestStreamTable"
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
        lake_db_name = "DeltalakeDB"
        lake_db = _glue.CfnDatabase(
            self,
            "DeltalakeDatabase",
            catalog_id=cdk.Aws.ACCOUNT_ID,
            database_input=_glue.CfnDatabase.DatabaseInputProperty(
                name=lake_db_name,
                location_uri=s3_bucket.s3_url_for_object(key="delta-lake-db/events")
            )
        )
        lake_db.apply_removal_policy(cdk.RemovalPolicy.DESTROY)
        # cdk.CfnOutput(self, "DeltalakeDatabaseName", value=lake_db.database_input.name)
        # cdk.CfnOutput(self, "DeltalakeDatabaseLocation", value=lake_db.database_input.location_uri)

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
                                    resource_name="DeltalakeGlueRole")
                            ]
                        )
                    ]
                )
            }
        )
        # cdk.CfnOutput(self, "GlueJobRoleName", value=glue_role.role_name)
        # cdk.CfnOutput(self, "GlueJobRoleArn", value=glue_role.role_arn)

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
                script_location=f"{s3_bucket.s3_url_for_object(key='scripts/') + etl_script_name}"
            ),
            role=glue_role.role_arn,
            connections=_glue.CfnJob.ConnectionsListProperty(
                connections=["deltalake-connector-1_0_0"] # Naming property for Delta Lake GDC Demo
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
                "--delta_s3_path": s3_bucket.s3_url_for_object(key="data-lake-db/events"),
                "--aws_region": cdk.Aws.REGION,
                "--window_size": "100 seconds",
                "--extra-jars": s3_bucket.s3_url_for_object(key="assets/aws-sdk-java-2.23.13.jar"),
                "--extra-jars-first": "false",
                "--enable-metrics": "true",
                "--enable-spark-ui": "true",
                "--spark-event-logs-path": s3_bucket.s3_url_for_object(key="spark-history-logs/"),
                "--enable-job-insights": "false",
                "--enable-glue-datacatalog": "true",
                "--enable-continuous-cloudwatch-log": "true",
                "--job-bookmark-option": "job-bookmark-disable",
                "--job-language": "python",
                "--TempDir": s3_bucket.s3_url_for_object(key="temporary")
            },
            execution_property=_glue.CfnJob.ExecutionPropertyProperty(
                max_concurrent_runs=1
            ),
            glue_version="4.0",
            max_retries=0,
            timeout=2880,
            worker_type="G.1X",
            number_of_workers=2
        )
        # cdk.CfnOutput(self, "GlueJobName", value=glue_etl_job.name)
        # cdk.CfnOutput(self, "GlueJobRoleArn", value=glue_role.role_arn)

        # Define the HTTP APi for data ingestion from the game client
        http_api = _api.CfnApi(
            self,
            "IngestionApi",
            name="DataIngestionHttpApi",
            description="HTTP API for game events data ingestion to the Deltalake",
            protocol_type="HTTP"
        )
        _api.CfnStage(
            self,
            "IngestionApiStage",
            api_id=http_api.ref,
            stage_name="prod",
            auto_deploy=True,
            access_log_settings=_api.CfnStage.AccessLogSettingsProperty(
                destination_arn=_logs.LogGroup(
                    self,
                    "IngestionApiLogs",
                    retention=_logs.RetentionDays.ONE_MONTH,
                    removal_policy=cdk.RemovalPolicy.DESTROY
                ).log_group_arn,
                format="$context.requestId $context.requestTime $context.resourcePath $context.httpMethod $context.status $context.protocol"
            )
        )

        # Declare the Authorizer for the custom identity solution
        authorizer = _api.CfnAuthorizer(
            self,
            "BackendAuthorizer",
            api_id=http_api.ref,
            name="BackendAuthorizer",
            authorizer_type="JWT",
            identity_source=[
                "$request.header.Authorization"
            ],
            jwt_configuration=_api.CfnAuthorizer.JWTConfigurationProperty(
                audience=[
                    "gamebackend"
                ],
                issuer=issuer_endpoint_url
            )
        )

        # Create the HTTP API integration IAM role
        integration_role = _iam.Role(
            self,
            "HttpIntegrationRole",
            role_name="HttpIntegrationRole",
            assumed_by=_iam.ServicePrincipal("apigateway.amazonaws.com")
        )
        integration_role.add_to_policy(
            statement=_iam.PolicyStatement(
                sid="ApiDirectWriteKinesis",
                actions=[
                    "kinesis:PutRecord"
                ],
                effect=_iam.Effect.ALLOW,
                resources=[
                    stream.stream_arn
                ]
            )
        )

        # Define the AWS Proxy sub-integration, and route for Kinesis PutRecord
        kinesis_integration = _api.CfnIntegration(
            self,
            "KinesisIntegration",
            api_id=http_api.ref,
            integration_type="AWS_PROXY",
            integration_subtype="Kinesis-PutRecord",
            integration_method="POST",
            credentials_arn=integration_role.role_arn,
            request_parameters={
                "StreamName": stream.stream_name,
                "Data": "$request.body.Data",
                "PartitionKey": "event_id"
            },
            payload_format_version="2.0"
        )
        _api.CfnRoute(
            self,
            "PutRecordRoute",
            api_id=http_api.ref,
            route_key="POST /record",
            authorization_type="JWT",
            authorizer_id=authorizer.ref,
            target=f"integrations/{kinesis_integration.ref}",
            authorization_scopes=["guest", "authenticated"]
        )

        # Deltalake Ingestion Endpoint URL 
        cdk.CfnOutput(self, "IngestionApiUrl", value=f"{http_api.attr_api_endpoint}/prod/record")
