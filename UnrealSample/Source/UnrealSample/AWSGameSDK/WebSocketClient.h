// A Websocket client class with authentication and callback mechanisms

#pragma once

#include "CoreMinimal.h"
#include "IWebSocket.h"

class WebSocketClient {
public:
    DECLARE_DELEGATE_OneParam(FOnMessageReceived, const FString&);
    // Constructor that receives an auth_token and a connection endpoint
    WebSocketClient(const FString& authToken, const FString& endpoint, FOnMessageReceived callback);
    void SendMessage(const FString& message);

private:

    TSharedPtr<IWebSocket> Socket;
    FOnMessageReceived callback;

    void OnMessageReceived(const FString& message);
};