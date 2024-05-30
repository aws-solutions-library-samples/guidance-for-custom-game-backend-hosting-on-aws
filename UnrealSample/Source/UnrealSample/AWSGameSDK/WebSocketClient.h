// A Websocket client class with authentication and callback mechanisms

#pragma once

#include "CoreMinimal.h"

class WebSocketClient {
public:
    DECLARE_DELEGATE_OneParam(FOnMessageReceived, const FString&);
    // Constructor that receives an auth_token and a connection endpoint
    WebSocketClient(const FString& authToken, const FString& endpoint, FOnMessageReceived callback);

private:

    FOnMessageReceived callback;

    void OnMessageReceived(const FString& message);
};