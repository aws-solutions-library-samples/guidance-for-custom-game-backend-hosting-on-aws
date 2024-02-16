// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

#include <cstring>
#include <aws/gamelift/server/model/GameSession.h>
#include <aws/gamelift/server/model/UpdateGameSession.h>

class Server
{
public:
    Server();

    bool InitializeGameLift(int listenPort, std::string logfile);
    void FinalizeGameLift();
    bool AcceptPlayerSession(const std::string& playerSessionId);
    void OnStartGameSession(Aws::GameLift::Server::Model::GameSession myGameSession);
    void OnUpdateGameSession(Aws::GameLift::Server::Model::UpdateGameSession myGameSession);
    void ExtractValuesFromMatchmakerData(std::string matchmakerData);
    void OnProcessTerminate();
    bool OnHealthCheck() { return true; }
    void TerminateGameSession();
    bool HasGameSessionStarted() { return mGameSessionStarted; } 

private:
    bool mGameSessionStarted;
    std::string backfillTicketId; // Used for terminating backfill when we end the session
    std::string matchmakingConfigurationArn; // Used for terminating backfill when we end the session

};