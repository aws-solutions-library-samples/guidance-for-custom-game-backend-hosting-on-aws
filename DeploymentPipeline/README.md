# Simple Deployment Pipeline

**NOTE:** Just a simple sample setup used for the Quick start.

This is the CDK application for the simple deployment pipeline used by the [Quick Start](../README.md#quick-start). It's not designed as a production pipeline, but you can freely use the scripts to start building your own CI/CD solution.

The CDK stack contains an *AWS CodeBuild* build project that uses this repository as the source. Obviously you would use your own forked private repository with authentication as the source in your own implementation. Also, in addition to just using a single AWS CodeBuild build step to deploy the solution, you might want to consider *AWS CodePipeline* or any other CI/CD orchestration tool of your choice.

Make sure to run `npm install` before deploying the pipeline with CDK to install all the dependencies.