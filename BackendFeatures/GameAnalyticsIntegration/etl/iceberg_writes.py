#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

import os
import sys
import re

from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.conf import SparkConf
from pyspark.sql.types import *
from pyspark.sql.functions import *


def get_kinesis_stream_name_from_arn(stream_arn):
  ARN_PATTERN = re.compile(r'arn:aws:kinesis:([a-z0-9-]+):(\d+):stream/([a-zA-Z0-9-_]+)')
  results = ARN_PATTERN.match(stream_arn)
  return results.group(3)


def setSparkIcebergConf() -> SparkConf:
  conf_list = [
    ("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"),
    (f"spark.sql.catalog.{CATALOG}", "org.apache.iceberg.spark.SparkCatalog"),
    (f"spark.sql.catalog.{CATALOG}.warehouse", ICEBERG_S3_PATH),
    (f"spark.sql.catalog.{CATALOG}.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog"),
    (f"spark.sql.catalog.{CATALOG}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO"),
    (f"spark.sql.catalog.{CATALOG}.lock-impl", "org.apache.iceberg.aws.glue.DynamoLockManager"),
    (f"spark.sql.catalog.{CATALOG}.lock.table", DYNAMODB_LOCK_TABLE),
    ("spark.sql.iceberg.handle-timestamp-without-timezone", "true")
  ]
  spark_conf = SparkConf().setAll(conf_list)
  return spark_conf


args = getResolvedOptions(sys.argv, [
  'JOB_NAME',
  'catalog',
  'database_name',
  'table_name',
  'partition_key',
  'kinesis_table_name',
  'kinesis_stream_arn',
  'starting_position_of_kinesis_iterator',
  'iceberg_s3_path',
  'lock_table_name',
  'aws_region',
  'window_size'
  ]
)

CATALOG = args['catalog']
ICEBERG_S3_PATH = args['iceberg_s3_path']
DATABASE = args['database_name']
TABLE_NAME = args['table_name']
PARTITION_KEY = args['partition_key']
DYNAMODB_LOCK_TABLE = args['lock_table_name']
KINESIS_TABLE_NAME = args['kinesis_table_name']
KINESIS_STREAM_ARN = args['kinesis_stream_arn']
KINESIS_STREAM_NAME = get_kinesis_stream_name_from_arn(KINESIS_STREAM_ARN)
STARTING_POSITION_OF_KINESIS_ITERATOR = args.get('starting_position_of_kinesis_iterator', 'LATEST')
AWS_REGION = args['aws_region']
WINDOW_SIZE = args.get('window_size', '100 seconds')

# Set the Spark, and Glue context
conf = setSparkIcebergConf()
sc = SparkContext(conf=conf)
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args['JOB_NAME'], args)

# Create the iceberg table (if it doesn't already exist)
# NOTE: Partitioned Iceberg tables can't currently be created in CDK 
# SEE: https://github.com/aws-cloudformation/cloudformation-coverage-roadmap/issues/1827
CREATE_DELTA_TABLE_SQL = f'''CREATE TABLE IF NOT EXISTS {CATALOG}.{DATABASE}.{TABLE_NAME} (
  event_id STRING,
  event_type STRING,
  updated_at TIMESTAMP,
  event_data STRING
) USING iceberg
PARTITIONED BY ({PARTITION_KEY})
OPTIONS ('format-version'='2')
'''

spark.sql(CREATE_DELTA_TABLE_SQL)

# Read from Kinesis Data Stream
streaming_data = spark.readStream \
                    .format("kinesis") \
                    .option("streamName", KINESIS_STREAM_NAME) \
                    .option("endpointUrl", f"https://kinesis.{AWS_REGION}.amazonaws.com") \
                    .option("startingPosition", STARTING_POSITION_OF_KINESIS_ITERATOR) \
                    .load()

streaming_data_df = streaming_data \
    .select(from_json(col("data").cast("string"), \
      glueContext.get_catalog_schema_as_spark_schema(DATABASE, KINESIS_TABLE_NAME)) \
    .alias("source_table")) \
    .select("source_table.*") \
    .withColumn('updated_at', to_timestamp(col('updated_at'), 'yyyy-MM-dd HH:mm:ss'))

table_identifier = f"{CATALOG}.{DATABASE}.{TABLE_NAME}"
checkpointPath = os.path.join(args["TempDir"], args["JOB_NAME"], "checkpoint/")

# NOTE: Writing against partitioned table
# (SEE https://iceberg.apache.org/docs/0.14.0/spark-structured-streaming/#writing-against-partitioned-table)
# Complete output mode not supported when there are no streaming aggregations on streaming DataFrame/Datasets
query = streaming_data_df.writeStream \
    .format("iceberg") \
    .outputMode("append") \
    .trigger(processingTime=WINDOW_SIZE) \
    .option("path", table_identifier) \
    .option("fanout-enabled", "true") \
    .option("checkpointLocation", checkpointPath) \
    .start()

query.awaitTermination()
