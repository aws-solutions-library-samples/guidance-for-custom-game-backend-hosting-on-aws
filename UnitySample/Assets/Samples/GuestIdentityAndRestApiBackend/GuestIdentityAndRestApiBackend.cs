// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;
using System;

public class GuestIdentityAndRestApiBackend : MonoBehaviour
{
    public string loginEndpointUrl;
    public string backendEndpointUrl;
    public Text logOutput;

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
        
    }

    // Triggered by the SDK if there's any login error or error refreshing access token
    void OnLoginOrRefreshError(string error)
    {
        Debug.LogError("Login or refresh error: " + error);
        this.logOutput.text += "Login or refresh error: " + error + "\n";

        // NOTE: You would here trigger a new log in or other remediation
    }

    void OnLoginResponse(LoginRequestData userInfo)
    {
        Debug.Log("Login response: UserID: " + userInfo.user_id + "GuestSecret: " + userInfo.guest_secret + "Token: " + userInfo.auth_token + " Refresh Token: " + userInfo.refresh_token + " Auth Token Expires In: " + userInfo.auth_token_expires_in + " Refresh Token Expires In: " + userInfo.refresh_token_expires_in);
        this.logOutput.text += "Login response: \nUserID: " + userInfo.user_id + " \nGuestSecret: " 
                                + userInfo.guest_secret + " \nToken: " + userInfo.auth_token + "\n"
                                + "Auth token expires in: " + userInfo.auth_token_expires_in + "\n"
                                + "Refresh token expires in: " + userInfo.refresh_token_expires_in + "\n";
        
        // Store identity to PlayerPrefs
        PlayerPrefs.SetString("user_id", userInfo.user_id);
        PlayerPrefs.SetString("guest_secret", userInfo.guest_secret);
        //PlayerPrefs.SetString("refresh_token", userInfo.refresh_token); // You could use this to login with refresh token instead of logging in a gain
        //PlayerPrefs.SetString("refresh_token_expires_datetime", DateTime.UtcNow.AddSeconds(userInfo.refresh_token_expires_in).ToString()); // You could use this to initialize new full login when running out of time
        PlayerPrefs.Save();

        // Test a backend get call
        var queryParameters = new Dictionary<string, string>();
        queryParameters.Add("player_name", "John Doe");
        AWSGameSDKClient.Instance.BackendGetRequest(this.backendEndpointUrl, "set-player-data", this.OnSetPlayerDataResponse, queryParameters);
    }

    void OnSetPlayerDataResponse(UnityWebRequest response)
    {
        Debug.Log("Backend set-player-data response: " + response.downloadHandler.text);
        this.logOutput.text += "Backend set-player-data response: " + response.downloadHandler.text + "\n";
        // Test requesting the player data now that it's set
        AWSGameSDKClient.Instance.BackendGetRequest(this.backendEndpointUrl, "get-player-data", this.OnGetPlayerDataResponse);
    }

    void OnGetPlayerDataResponse(UnityWebRequest response)
    {
        Debug.Log("Backend get-player-data response: " + response.downloadHandler.text); 
        this.logOutput.text  += "Backend get-player-data response: " + response.downloadHandler.text + "\n";
    }
}
