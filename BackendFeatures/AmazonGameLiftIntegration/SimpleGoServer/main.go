package main

import (
	"flag"

	"github.com/aws-solutions-library-samples/guidance-for-custom-game-backend-hosting-on-aws/server"
)

func main() {

	// Read command line flags and run server
	portPtr := flag.Int("port", 1935, "Server port to listen on.")
	flag.Parse()
	server.Run(*portPtr)
}
