// Implement WebsocketClient.cpp with constructor and destructor
#include "WebsocketClient.h"
#include "CoreMinimal.h"
#include "WebSocketsModule.h"
#include "IWebSocket.h"
#include <iostream>

// Constructor that receives auth token and connection endpoint and sets up the WebSocket
WebSocketClient::WebSocketClient(const FString& authToken, const FString& endpoint, FOnMessageReceived callback) {

    // Set the callback for messages
    this->callback = callback;

    // Generate a connection url with auth token passed
    const FString ServerURL = endpoint+"/?auth_token="+*authToken; 
    const FString ServerProtocol = TEXT("wss");
        
    // Create the WebSocket
    TSharedPtr<IWebSocket> Socket = FWebSocketsModule::Get().CreateWebSocket(ServerURL, ServerProtocol);

    // Bind our message handler
    Socket->OnMessage().AddRaw(this, &WebSocketClient::OnMessageReceived);


    // Optional bindings you can define
    Socket->OnConnected().AddLambda([]() -> void {
        // Add code that would run once connected.
    });
        
    Socket->OnConnectionError().AddLambda([](const FString & Error) -> void {
        // Add code that would run if the connection failed. Check Error to see what happened.
    });
        
    Socket->OnClosed().AddLambda([](int32 StatusCode, const FString& Reason, bool bWasClean) -> void {
        // Add code that would run when the connection to the server has been terminated.
        // Because of an error or a call to Socket->Close().
    });
        
    Socket->OnRawMessage().AddLambda([](const void* Data, SIZE_T Size, SIZE_T BytesRemaining) -> void {
        // Add code that would run when we receive a raw (binary) message from the server.
    });
        
    Socket->OnMessageSent().AddLambda([](const FString& MessageString) -> void {
        // Add code that is called after we sent a message to the server.
    });
        
    // Now that we have the bindings, connect to the server
    Socket->Connect();
}

void WebSocketClient::OnMessageReceived(const FString & Message) {

    // Call the callback function with the received message
    this->callback.ExecuteIfBound(Message);
}
