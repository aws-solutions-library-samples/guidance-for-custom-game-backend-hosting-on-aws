FROM public.ecr.aws/amazonlinux/amazonlinux:latest as build-server

# Install dependencies
RUN yum install -y gcc-c++ gdb cmake3 git wget openssl openssl-devel

# Copy local code files over
COPY . /server

#RUN cd /usr/lib64/ && \
#echo $(ls)

# Download and build the GameLift Server SDK
RUN echo "Download and unzip GameLift Server SDK" && \
mkdir SDK && cd SDK && \
wget https://gamelift-server-sdk-release.s3.us-west-2.amazonaws.com/cpp/GameLift-Cpp-ServerSDK-5.1.1.zip && \
unzip GameLift-Cpp-ServerSDK-5.1.1.zip && \
echo "Build the GameLift server SDK" && \
mkdir cmake-build && \
cmake -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -S . -B ./cmake-build && \
cmake --build cmake-build --target all && \
cd ./cmake-build/prefix/ && \
echo "Copying files over to server folder" && \
mkdir /server/lib && \
mkdir /server/include && \
cp -r ./lib/*  /server/lib/ && \
cp -r ./include/*  /server/include/

# Copy the Open SSL files to server folder
RUN cp /usr/lib64/libssl.so.3 /server/ && \
cp /usr/lib64/libcrypto.so.3 /server/

# Build the binary
RUN echo "Building the server binary" && \
cd /server && \
g++ -o GameLiftSampleServer Server.h Server.cpp -Iinclude -Llib -laws-cpp-sdk-gamelift-server -lssl -lcrypto -pthread -Wl,-rpath=./lib   

# Copy the binaries only to a clean setup for copying to local system after build
FROM scratch AS server
  COPY --from=build-server /server/ /
  ENTRYPOINT [ "/GameLiftSampleServer" ]

