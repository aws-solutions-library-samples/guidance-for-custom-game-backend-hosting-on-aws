module github.com/aws-solutions-library-samples/guidance-for-custom-game-backend-hosting-on-aws

replace aws/amazon-gamelift-go-sdk => ../GameLift-Go-ServerSDK-5.0.0

go 1.18

require aws/amazon-gamelift-go-sdk v0.0.0-00010101000000-000000000000

require (
	github.com/google/uuid v1.3.0 // indirect
	github.com/gorilla/websocket v1.5.0 // indirect
)
