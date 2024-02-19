import aws_cdk as core
import aws_cdk.assertions as assertions

from delta_lake_integration.delta_lake_integration_stack import DeltaLakeIntegrationStack

# example tests. To run these tests, uncomment this file along with the example
# resource in delta_lake_integration/delta_lake_integration_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = DeltaLakeIntegrationStack(app, "delta-lake-integration")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
