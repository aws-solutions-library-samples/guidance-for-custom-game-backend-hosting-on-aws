package server

import (
	"aws/amazon-gamelift-go-sdk/model"
	"aws/amazon-gamelift-go-sdk/model/request"
	"aws/amazon-gamelift-go-sdk/server"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	ConnectionType = "tcp"
)

type gameProcess struct {
	// Port listening for player connections
	Port int

	// Log files to upload when game session ends
	Logs server.LogParameters
}

var acceptingConnections bool = false
var gameSessionRunning bool = false
var backfillTicketId string = ""
var matchmakingConfigurationArn string = ""

// Process GameLift activation request when game session is created
func (g gameProcess) OnStartGameSession(session model.GameSession) {

	gameSessionRunning = true
	matchmakerData := session.MatchmakerData
	log.Println("MatchmakerData: ", matchmakerData)

	err := ExtractMatchmakerData(matchmakerData)
	if err != nil {
		log.Fatalln(err.Error())
	}

	// Ready to receive player connections
	err = server.ActivateGameSession()
	if err != nil {
		log.Fatalln(err.Error())
	} else {
		log.Println("OnStartGameSession Success")
	}

	// Listening could start here, rather than in the Run() method,
	// go startListening(g)
}

// Process GameLift game session updates
func (g gameProcess) OnUpdateGameSession(session model.UpdateGameSession) {

	log.Println("OnUpdateGameSession")

	if session.BackfillTicketID != "" {
		log.Println("Updating backfill ticket ID: ", session.BackfillTicketID)
		backfillTicketId = session.BackfillTicketID
	}
}

// Process GameLift shut down request
func (g gameProcess) OnProcessTerminate() {

	log.Println("[GAMELIFT] OnProcessTerminate")

	// Game-specific tasks required to gracefully shut down a game session,
	// such as notifying players, preserving game state data, and other cleanup
	if gameSessionRunning {
		log.Println("GameLift activated, terminating process")
		g.TerminateGameSession()

		// We will exit here as GameLift will start a new game server process right after
		log.Println("Done!")
		os.Exit(0)
	}
}

// Provide health status to GameLift
func (g gameProcess) OnHealthCheck() bool {
	log.Println("GameLift healthcheck succeeded")
	return true
}

// Creates a TCP server and received connections from players
func (g gameProcess) SetupTcpServerAndStartAcceptingPlayers() error {

	ip := "0.0.0.0"
	address := fmt.Sprintf("%v:%v", ip, g.Port)

	l, err := net.Listen(ConnectionType, address)
	if err != nil {
		return fmt.Errorf("Failed listening on port %s - %w", address, err)
	} else {
		acceptingConnections = true
	}

	// Close listener on function return
	defer l.Close()

	for acceptingConnections {
		log.Println("Waiting for next player to join...")

		conn, err := l.Accept()
		if err != nil {
			return fmt.Errorf("Accepting new connection failed: %s", err)
		}

		log.Println("Accepted connection")
		go handleConnection(conn)
	}

	return nil
}

func ExtractMatchmakerData(matchmakerData string) error {
	// Unmarshal the JSON string into a map[string]any type and retrieve the value of the "autoBackfillTicketId" key
	var result map[string]any
	err := json.Unmarshal([]byte(matchmakerData), &result)
	if err != nil {
		log.Println("Error reading:", err.Error())
		return err
	}

	backfillTicketId = result["autoBackfillTicketId"].(string)
	log.Println("AutoBackFillTicketId: ", backfillTicketId)

	matchmakingConfigurationArn = result["matchmakingConfigurationArn"].(string)
	log.Println("MatchmakingConfigurationArn: ", matchmakingConfigurationArn)

	return nil
}

func startListening(process gameProcess) {

	err := process.SetupTcpServerAndStartAcceptingPlayers()
	if err != nil {
		log.Fatalln(err.Error())
		os.Exit(1)
	}
}

// Handles incoming connection requests
func handleConnection(conn net.Conn) {

	const accepted = "Your connection was accepted and token valid"
	const notaccepted = "Your token is invalid"

	// We read just one message from the client
	buf := make([]byte, 1024)
	_, err := conn.Read(buf)
	if err != nil {
		log.Println("Error reading:", err.Error())
		return
	}

	defer conn.Close()

	playerSessionId := strings.Split(string(buf), "\n")[0]
	log.Println("Player session id: ", playerSessionId)

	// Try to accept the player session ID through GameLift and inform the client of the result
	// You could use this information to drop any clients that are not authorized to join this session
	err = server.AcceptPlayerSession(playerSessionId)
	if err != nil {
		conn.Write([]byte(notaccepted))
		log.Println("Didn't accept player session token: ", playerSessionId, err.Error())
	} else {
		conn.Write([]byte(accepted))
		log.Println("Accepted player session token")
	}
}

func (g gameProcess) TerminateGameSession() {

	acceptingConnections = false

	if backfillTicketId != "" {
		log.Println("Terminating backfill as we're closing the process")

		gameSessionArn, err := server.GetGameSessionID()
		if err != nil {
			log.Fatalln(err.Error())
		}

		stopBackfillRequest := request.NewStopMatchBackfill()
		stopBackfillRequest.TicketID = backfillTicketId
		stopBackfillRequest.GameSessionArn = gameSessionArn
		stopBackfillRequest.MatchmakingConfigurationArn = matchmakingConfigurationArn

		err = server.StopMatchBackfill(stopBackfillRequest)
		if err != nil {
			log.Fatalln(err.Error())
		}
	}

	log.Println("Terminating game session")

	// Sleep for a few seconds to let CW Logs agent send final logs before we terminate
	time.Sleep(3 * time.Second)

	server.ProcessEnding()
	gameSessionRunning = false
}

func (g gameProcess) HasStartedGameSession() bool {
	gameSessionID, err := server.GetGameSessionID()
	return err == nil && gameSessionID != ""
}

// Configures gamelift
func setupGamelift(process gameProcess) {

	log.Println("Starting server...")

	// For servers hosted on Amazon GameLift managed EC2 instances, use an empty object.
	serverParameters := server.ServerParameters{}

	err := server.InitSDK(serverParameters)
	if err != nil {
		log.Fatalln(err.Error())
		os.Exit(1)
	}

	log.Println("InitSDK Done!")
	log.Println("Process Ready...")

	err = server.ProcessReady(server.ProcessParameters{
		OnStartGameSession:  process.OnStartGameSession,
		OnUpdateGameSession: process.OnUpdateGameSession,
		OnProcessTerminate:  process.OnProcessTerminate,
		OnHealthCheck:       process.OnHealthCheck,
		LogParameters:       process.Logs,
		Port:                process.Port,
	})

	if err != nil {
		log.Fatalln(err.Error())
		os.Exit(1)
	} else {
		log.Println("Process Ready Done!")
	}
}

func Run(port int) {

	logfile := fmt.Sprintf("./logs/myserver%d.log", port)
	log.Printf("Starting Go game server, see %s for output", logfile)

	// Create or open log file for writing
	err := os.MkdirAll("./logs", fs.ModePerm)
	if err != nil {
		log.Fatalln(err.Error())
		os.Exit(1)
	}

	// Open or create log file
	outfile, err := os.OpenFile(logfile, os.O_RDWR|os.O_CREATE|os.O_APPEND, fs.ModePerm)
	if err != nil {
		log.Fatalln(err.Error())
		os.Exit(1)
	}

	defer outfile.Close()
	log.SetOutput(outfile)

	logfile, err = filepath.Abs(logfile)
	if err != nil {
		log.Fatalln(err.Error())
		os.Exit(1)
	}

	log.Println("Server port: ", port)

	process := gameProcess{
		Port: port,
		Logs: server.LogParameters{
			LogPaths: []string{logfile},
		},
	}

	// Gracefully destroy server when application quits
	defer server.Destroy()
	// Initialize and configure gamelift
	setupGamelift(process)

	// Listening should only really start once a game session has started
	// but this matches the C++ implementation
	go startListening(process)

	for {
		// Check if we have a started game session and wait for a minute to end game
		if process.HasStartedGameSession() {
			log.Println("Game session started! We'll just wait 60 seconds to give time for players to connect in the other thread and terminate")
			time.Sleep(60 * time.Second)

			log.Println("Game Session done! Clean up session and shutdown")
			// Inform GameLift we're shutting down so it can replace the process with a new one
			process.TerminateGameSession()
			os.Exit(0)
		} else {
			// Otherwise just sleep 10 seconds and keep waiting
			time.Sleep(10 * time.Second)
		}
	}
}
