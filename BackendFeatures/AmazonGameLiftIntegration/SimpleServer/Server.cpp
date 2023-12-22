// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

#define GAMELIFT_USE_STD

#include <aws/gamelift/server/GameLiftServerAPI.h>
#include "Server.h"

#include <unistd.h>
#include <stdio.h>
#include <iostream>
#include <cstdlib>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <stdlib.h>
#include <netinet/in.h>
#include <string.h>

// Accepts a new player connection and validates the player session ID received from the player
void AcceptNewPlayerConnection(int server_fd, int addrlen, sockaddr_in address, Server *server)
{
    int new_socket, valread;
    char buffer[1024] = {0};
    std::string accepted = "Your connection was accepted and token valid";
    std::string notaccepted = "Your token is invalid";
    
    if ((new_socket = accept(server_fd, (struct sockaddr *)&address, 
                       (socklen_t*)&addrlen))<0)
    {
        std::cout << "Accepting new connection failed\n";
        return;
    }
    
    // We read just one message from the client with blocking I/O
    // For an actual game server you will want to use Boost.Asio, game engine-specific libraries, or other asynchronous higher level library for the socket communication
    valread = read( new_socket , buffer, 1024);
    std::cout << buffer << std::endl;

    // Try to accept the player session ID through GameLift and inform the client of the result
    // You could use this information to drop any clients that are not authorized to join this session
    bool success = server->AcceptPlayerSession(buffer);
    if(success)
    {
        send(new_socket , accepted.c_str() , strlen(accepted.c_str()) , 0 );
        std::cout << "Accepted player session token\n";
    }
    else
    {
         send(new_socket , notaccepted.c_str() , strlen(notaccepted.c_str()) , 0 );
        std::cout << "Didn't accept player session token\n";
    }
}

// Creates a TCP server and received connections from two players
int SetupTcpServerAndStartAcceptingPlayers(Server *server, int PORT)
{
    int server_fd;
    struct sockaddr_in address;
    int addrlen = sizeof(address);
       
    // Create Socket (AF_INET = IPv4, SOCK_STREAM = TCP, 0 = only supported protocol (TCP))
    if ((server_fd = socket(AF_INET, SOCK_STREAM, 0)) == 0)
    {
        std::cout << "socket creation failed";
        return -1;
    }
       
    // Setup Socket options to reuse address and port
    int options = 1;
    if (setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR | SO_REUSEPORT,
                                                  &options, sizeof(options)))
    {
        std::cout<< "Setting socket options failed";
        return -1;
    }
    
    // Configure address
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons( PORT );
       
    // Bind socket to any address
    if (bind(server_fd, (struct sockaddr *)&address, 
                                 sizeof(address))<0)
    {
       std::cout << "Binding failed";
       return -1;
    }
    
    // Start listening with a max backlog of 2 connections
    if (listen(server_fd, 2) < 0)
    {
        std::cout << "listen failed";
        return -1;
    }
    
    // Just keep accepting players in this thread until the game server loop ends the server
    while(true){
        std::cout << "Waiting for next player to join..." << std::endl;
        // Accept first player
        AcceptNewPlayerConnection(server_fd, addrlen, address, server);
    }

}

int main (int argc, char* argv[]) {
    
    std::cout << "Starting game server, see /logs/myserver1935.log for output" << std::endl;
    
    // Read port from args
    int PORT = 1935; //Default to 1935
    for(int counter=0;counter<argc;counter++)
    {
        if(strcmp(argv[counter], "-port") == 0)
        {
            // Read the next arg which is the port number
            PORT = atoi(argv[counter+1]);
        }
    }
    
    // Forward logs to correct folder for GameLift and CloudWatch Agent to find
    mkdir("./logs", 0777);
    std::string logfile = std::string("logs/myserver");
    logfile += std::to_string(PORT) + ".log";
    freopen(logfile.c_str(),"w",stdout);
    freopen(logfile.c_str(),"w",stderr);
    
    std::cout << "Server port: " << PORT << std::endl;

    // GameLift setup
    std::cout << "Starting server...\n";
	Server *server = new Server();
	server->InitializeGameLift(PORT, logfile);
	
	// NOTE: You should Wait for a game to start before accepting connetions
	
	// Setup the simple blocking TCP Server thread and start accepting and validating players
	std::thread my_thread(SetupTcpServerAndStartAcceptingPlayers,server, PORT);
    //int serverResult = SetupTcpServerAndAcceptPlayers(server, PORT);
    
    while (true) {
    
        //std::cout << "Waiting for game session to start..." << std::endl;
        
        // Check if we have a started game session and wait for a minute to end game
        if(server->HasGameSessionStarted()) {
            std::cout << "Game session started! We'll just wait 60 seconds to give time for players to connect in the other thread and terminate" << std::endl;
            sleep(60);
                
            std::cout << "Game Session done! Clean up session and shutdown" << std::endl;
            // Inform GameLift we're shutting down so it can replace the process with a new one
            server->TerminateGameSession();

            exit(0);
        }
        // Otherwise just sleep 10 seconds and keep waiting
        sleep(10);

    }


    return 0;
}




/// SERVER CLASS FOR GAMELIFT FUNCTIONALITY ////

Server::Server() : mGameSessionStarted(false)
{
}

bool Server::InitializeGameLift(int listenPort, std::string logfile)
{
	try
	{
	    std::cout << "Init GameLift SDK...\n";
		auto initOutcome = Aws::GameLift::Server::InitSDK();

		if (!initOutcome.IsSuccess())
		{
			return false; 
		}

        std::cout << "InitSDK Done!\n";
        
        // Set parameters and call ProcessReady
        std::string serverLog(logfile);
        std::vector<std::string> logPaths;
        logPaths.push_back(serverLog);

		auto processReadyParameter = Aws::GameLift::Server::ProcessParameters(
			std::bind(&Server::OnStartGameSession, this, std::placeholders::_1),
            std::bind(&Server::OnUpdateGameSession, this, std::placeholders::_1),
			std::bind(&Server::OnProcessTerminate, this),
			std::bind(&Server::OnHealthCheck, this),
			listenPort, Aws::GameLift::Server::LogParameters(logPaths)
		);

        std::cout << "Process Ready...\n";
		auto readyOutcome = Aws::GameLift::Server::ProcessReady(processReadyParameter);

		if (!readyOutcome.IsSuccess())
			return false;
			
		std::cout << "Process Ready Done!\n";

		return true;

	}
	catch (int exception)
	{
		std::cout << "Exception Code: " << exception << std::endl; 
		return false;
	}
}

void Server::FinalizeGameLift()
{
	Aws::GameLift::Server::Destroy();
}


bool Server::AcceptPlayerSession(const std::string& playerSessionId)
{
	auto outcome = Aws::GameLift::Server::AcceptPlayerSession(playerSessionId);

	if (outcome.IsSuccess())
	{
		return true;
	}

    std::cout << "[GAMELIFT] AcceptPlayerSession Fail: " << outcome.GetError().GetErrorMessage().c_str() << std::endl;
	return false;
}

void Server::OnStartGameSession(Aws::GameLift::Server::Model::GameSession myGameSession)
{
	mGameSessionStarted = true;
    std::string matchmakerData = myGameSession.GetMatchmakerData();
    std::cout << "MatchmakerData: " << matchmakerData << std::endl;
    this->ExtractValuesFromMatchmakerData(matchmakerData);
    
    Aws::GameLift::Server::ActivateGameSession();
	std::cout << "OnStartGameSession Success\n";
}

void Server::ExtractValuesFromMatchmakerData(std::string matchmakerData)
{
    // Search for AutoBackFillTicketId and manually parse the JSON value (you should use any JSON parser you have in your project)
    std::string autoBackFillTicketId = "";
    int start = matchmakerData.find("autoBackfillTicketId");
    if(start != std::string::npos)
    {
        int end = matchmakerData.find("}", start);
        autoBackFillTicketId = matchmakerData.substr(start, end-start);
        autoBackFillTicketId = autoBackFillTicketId.substr(autoBackFillTicketId.find(":")+2);
        autoBackFillTicketId = autoBackFillTicketId.substr(0, autoBackFillTicketId.find("\""));
        std::cout << "AutoBackFillTicketId: " << autoBackFillTicketId << std::endl;
        this->backfillTicketId = autoBackFillTicketId;
    }
    // Search for MatchmakingConfigurationArn and manually parse the JSON value
    std::string matchmakingConfigurationArn = "";
    start = matchmakerData.find("matchmakingConfigurationArn");
    if(start != std::string::npos)
    {
        int end = matchmakerData.find("}", start);
        matchmakingConfigurationArn = matchmakerData.substr(start, end-start);
        matchmakingConfigurationArn = matchmakingConfigurationArn.substr(matchmakingConfigurationArn.find(":")+2);
        matchmakingConfigurationArn = matchmakingConfigurationArn.substr(0, matchmakingConfigurationArn.find("\""));
        std::cout << "MatchmakingConfigurationArn: " << matchmakingConfigurationArn << std::endl;
        this->matchmakingConfigurationArn = matchmakingConfigurationArn;
    }
}

void Server::OnUpdateGameSession(Aws::GameLift::Server::Model::UpdateGameSession updateGameSession)
{
	std::cout << "OnUpdateGameSession \n";

    if (updateGameSession.GetBackfillTicketId().empty() == false)
    {
        std::cout << "Updating backfill ticked ID: " + updateGameSession.GetBackfillTicketId();
        this->backfillTicketId = updateGameSession.GetBackfillTicketId();
    }
}

// Called when GameLift ends your process as part of a scaling event or terminating the Fleet
void Server::OnProcessTerminate()
{
    std::cout << "[GAMELIFT] OnProcessTerminate\n";
	// game-specific tasks required to gracefully shut down a game session, 
	// such as notifying players, preserving game state data, and other cleanup
	if (mGameSessionStarted)
	{
		std::cout << "GameLift activated, terminating process\n";
		TerminateGameSession();
		std::cout << "Done!\n";
		// We will exit here as GameLift will start a new game server process right after
		std::exit(0);
	}
}

void Server::TerminateGameSession()
{
    // Terminate backfill if we have an existing backfill ticket
    if(this->backfillTicketId.empty() == false)
    {
        std::cout << "Terminating backfill as we're closing the process\n";
        auto stopMatchBackfillRequest = new Aws::GameLift::Server::Model::StopMatchBackfillRequest();
        stopMatchBackfillRequest->SetTicketId(this->backfillTicketId);
        stopMatchBackfillRequest->SetGameSessionArn(Aws::GameLift::Server::GetGameSessionId().GetResult());
        stopMatchBackfillRequest->SetMatchmakingConfigurationArn(this->matchmakingConfigurationArn);
        Aws::GameLift::Server::StopMatchBackfill(*stopMatchBackfillRequest);
    }
    std::cout << "Terminating game session\n";
    sleep(3); // Sleep for a few seconds to let CW Logs agent send final logs before we terminate
	Aws::GameLift::Server::ProcessEnding();
	mGameSessionStarted = false;
}
