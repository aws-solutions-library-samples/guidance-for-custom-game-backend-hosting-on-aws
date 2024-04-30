// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;
using System;
using Unity.VisualScripting;

public class SimpleWebsocketChat : MonoBehaviour
{
    public string loginEndpointUrl;
    public string websocketEndpointUrl;
    public Text logOutput;

    // A list of messages received from the server
    private List<string> messages = new List<string>();
    private float messageTimer = 0.0f;

    private bool connected = false;

    // Start is called before the first frame update
    void Start()
    {
        // Set the login endpoint
        Debug.Log("Setting login endpoint");
        AWSGameSDKClient.Instance.Init(loginEndpointUrl, this.OnLoginOrRefreshError);

        // If we have existing identity, request auth token for that
        if(PlayerPrefs.GetString("user_id", "") != "" && PlayerPrefs.GetString("guest_secret", "") != "")
        {
            Debug.Log("Requesting auth token for existing identity: " + PlayerPrefs.GetString("user_id"));
            this.logOutput.text += "Requesting auth token for existing identity: " + PlayerPrefs.GetString("user_id") + "\n";
        
            AWSGameSDKClient.Instance.LoginAsGuest(PlayerPrefs.GetString("user_id"), PlayerPrefs.GetString("guest_secret"), this.OnLoginResponse);
        }
        else
        {
            Debug.Log("Requesting new identity");
            this.logOutput.text += "Requesting new identity\n";
            AWSGameSDKClient.Instance.LoginAsNewGuestUser(this.OnLoginResponse);
        }
    }

    // Update is called once per frame
    void Update()
    {
        //Iterate through the messages and add them to the logOutput
        foreach (string message in this.messages)
        {
            this.logOutput.text += message + "\n";
        }
        // Clean up the messages list
        this.messages.Clear();

        messageTimer -= Time.deltaTime;
        // Every 1 seconds send a message to the server
        if (messageTimer <= 0 && this.connected)
        {
            string message = "Hello from Unity!";
            this.logOutput.text += "Sending message to server: " + message + "\n"; 
            var success = GameObject.Find("WebsocketClient").GetComponent<WebsocketClient>().SendMessage(message);
            if(!success) {
                Debug.LogError("Failed to send message to server");
                this.logOutput.text += "Failed to send message to server\n";
            }

            messageTimer = 1.0f;

        }  
    }

    // Triggered by the SDK if there's any login error or error refreshing access token
    void OnLoginOrRefreshError(string error)
    {
        Debug.LogError("Login or refresh error: " + error);
        this.logOutput.text += "Login or refresh error: " + error + "\n";

        // NOTE: You would here trigger a new log in or other remediation
    }

    async void OnLoginResponse(LoginRequestData userInfo)
    {
        Debug.Log("Login response: UserID: " + userInfo.user_id + "GuestSecret: " + userInfo.guest_secret);
        this.logOutput.text += "Login response: \nUserID: " + userInfo.user_id + " \nGuestSecret: " 
                                + userInfo.guest_secret + "\n";
        
        // Store identity to PlayerPrefs
        PlayerPrefs.SetString("user_id", userInfo.user_id);
        PlayerPrefs.SetString("guest_secret", userInfo.guest_secret);
        PlayerPrefs.Save();

        // Create the Websocket client, TODO: Could optimally be managed by the SDK with callbacks!
        //AWSGameSDKClient.Instance.InitWebsocketClient(websocketEndpointUrl, userInfo.auth_token, this.OnWebsocketError);
        GameObject.Find("WebsocketClient").GetComponent<WebsocketClient>().CreateWebSocketConnection(this.websocketEndpointUrl, userInfo.auth_token, this.OnWebsocketMessage);

    }

    // Define a callback for Websocket messages
    void OnWebsocketMessage(string message)
    {
        Debug.Log("Websocket message: " + message);
        // Add to the messages list so we can display this in the main thread
        this.messages.Add(message);
        
        // TODO: Check the message and mark us as successfully connected
        this.connected = true;
    }
}
