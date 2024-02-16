using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using System.Net.Sockets;
using System;
using System.Text;
using System.IO;
using System.Threading;

// A simple TCP client to connect to the simple sample server and send playerSessionId for validation
public class SimpleServerClient
{
    private string ipAddress;
    private int port;

    private Text logOutput;

    private TcpClient client;

    public SimpleServerClient(string ipAddress, int port, Text logOutput) {
        this.ipAddress = ipAddress;
        this.port = port;
        this.logOutput = logOutput;
    }

    // Simple blocking client that connects to the server and sends the playerSessionId    
    public void ConnectToServer() {
        
        Debug.Log("Connecting to server...");
        // Define a TCP client that connects to the server
        TcpClient client = new TcpClient();
        var result = client.BeginConnect(this.ipAddress, this.port, null, null);

        var success = result.AsyncWaitHandle.WaitOne(TimeSpan.FromSeconds(2));

        if (!success)
        {
            this.logOutput.text += "Failed to connect to server." + "\n";
            throw new Exception("Failed to connect.");
        }
        client.NoDelay = true; // Use No Delay to send small messages immediately. UDP should be used for even faster messaging
        Debug.Log("Done");
        this.logOutput.text += "Successfully connected!" + "\n";
        this.client = client;
    }

    public void DisconnectFromServer(){
        this.client.Close();
    }

    public void SendMessage(string message){
        this.SendMessage(this.client, message);
    }

    public string ReceiveMessage(){
        return this.ReceiveMessage(this.client);
    }

    private void SendMessage(TcpClient client, string message){
        NetworkStream stream = client.GetStream();
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, true)) {
            // The sample C++ server expects to receive a char array
            writer.Write(message.ToCharArray());
        }
    }

    private string ReceiveMessage(TcpClient client){
        try
        {
            Debug.Log("Starting message receive...");
            NetworkStream stream = client.GetStream();
            while (stream.DataAvailable) {
                try {
                    using (var reader = new BinaryReader(stream, Encoding.ASCII, true)) {
                        Debug.Log("Found message, reading it..");
                        var bytes = new byte[client.ReceiveBufferSize];
                        stream.Read(bytes, 0, client.ReceiveBufferSize);             
                        string message = Encoding.ASCII.GetString(bytes);
                        Debug.Log("Received message: " + message);
                        return message;
                    }
                }
                catch (Exception e)
                {
                    Debug.Log("Error receiving a message: " + e.Message);
                    Debug.Log("Aborting");
                    return "Error: " + e.Message;
                }
            }
        }
        catch (Exception e) {
            System.Console.WriteLine("Error accessing message stream: " + e.Message);
        }

        return null;
    }
}
