using System;
using System.Collections;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using System.IO;

public class WebsocketClient : MonoBehaviour
{
    private ClientWebSocket webSocket;

    private async void Start()
    {
        //await CreateWebSocketConnection();
    }

    public async Task CreateWebSocketConnection(string ServerUrl, string AuthToken, Action<string> callback)
    {
        webSocket = new ClientWebSocket();
        try
        {
            await webSocket.ConnectAsync(new Uri(ServerUrl+"/?auth_token="+AuthToken), CancellationToken.None);
            Debug.Log("WebSocket connection opened.");

            // Start the threads for Send and receive
            Task.Run(() => ReceiveLoop(callback));
            //Task.Run(() => SendLoop());
        }
        catch (Exception ex)
        {
            Debug.LogError($"WebSocket error: {ex.Message}");
        }
    }

    private async Task ReceiveLoop(Action<string> callback)
    {
        var buffer = new ArraySegment<byte>(new byte[1024]);
        while (webSocket.State == WebSocketState.Open)
        {
            WebSocketReceiveResult result;
            using (var ms = new MemoryStream())
            {
                do
                {
                    result = await webSocket.ReceiveAsync(buffer, CancellationToken.None);
                    ms.Write(buffer.Array, buffer.Offset, result.Count);
                } while (!result.EndOfMessage);

                ms.Seek(0, SeekOrigin.Begin);
                using (var reader = new StreamReader(ms, Encoding.UTF8))
                {
                    var message = reader.ReadToEnd();
                    Debug.Log($"Received message from server: {message}");

                    // Trigger the callback
                    callback(message);
                }
            }
        }
    }

    public bool SendMessage(string message)
    {
        try {
            var buffer = Encoding.UTF8.GetBytes(message);
            webSocket.SendAsync(new ArraySegment<byte>(buffer, 0, buffer.Length), WebSocketMessageType.Text, true, CancellationToken.None);
        }catch(Exception e){
            Debug.Log(e);
            return false;
        }
        return true;
    }

    private async Task<string> GetMessageFromUser()
    {
        // Implement your logic to get a message from the user or other sources
        // For example, you can use Unity's Input.GetKeyDown or UI input fields
        //return await Task.FromResult(string.Empty);
        return string.Empty;
    }

    private void OnApplicationQuit()
    {
        webSocket?.Abort();
    }
}