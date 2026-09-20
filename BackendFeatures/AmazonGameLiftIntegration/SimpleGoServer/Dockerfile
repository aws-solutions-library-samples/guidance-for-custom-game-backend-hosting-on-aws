FROM public.ecr.aws/amazonlinux/amazonlinux:latest as build-server

# Install dependencies
RUN yum install -y golang wget

# Copy local code files over
COPY . /SimpleGoServer

# Download the GameLift SDK, NOTE: You can replace this with the latest version
RUN echo "Download and unzip GameLift Server SDK" && \
wget https://gamelift-server-sdk-release.s3.us-west-2.amazonaws.com/go/GameLift-Go-ServerSDK-5.0.0.zip && \
unzip GameLift-Go-ServerSDK-5.0.0.zip && \
rm GameLift-Go-ServerSDK-5.0.0.zip

# Build the binary
RUN echo "Building the Go server and copying output to LinuxServerBuild..." && \
cd /SimpleGoServer && \
env GOOS=linux GOARCH=amd64 go build -o ./GameLiftSampleServer

# Copy the binaries only to a clean setup for copying to local system after build
FROM scratch AS server
  COPY --from=build-server /SimpleGoServer/GameLiftSampleServer /
  ENTRYPOINT [ "/GameLiftSampleServer" ]

