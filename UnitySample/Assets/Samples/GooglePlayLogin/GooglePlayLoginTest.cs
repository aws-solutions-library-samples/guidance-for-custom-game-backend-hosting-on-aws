// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

public class GooglePlayLoginTest : MonoBehaviour
{
    public string loginEndpointUrl;
    public Text logOutput;

    // Start is called before the first frame update
    void Start()
    {
        // Set the login endpoint
        Debug.Log("Setting login endpoint");
        AWSGameSDKClient.Instance.Init(loginEndpointUrl, this.OnLoginOrRefreshError);

        // Get a new guest user first to test account linking
        Debug.Log("Requesting new guest identity first");
        this.logOutput.text += "Requesting new identity\n";
        AWSGameSDKClient.Instance.LoginAsNewGuestUser(this.OnGuestLoginResponse);
    }

    // Triggered by the SDK if there's any login error or error refreshing access token
    void OnLoginOrRefreshError(string error)
    {
        Debug.LogError("Login or refresh error: " + error);
        this.logOutput.text += "Login or refresh error: " + error + "\n";

        // NOTE: You would here trigger a new log in or other remediation
    }

    // Update is called once per frame
    void Update()
    {
        
    }

    void OnGuestLoginResponse(LoginRequestData userInfo)
    {
        Debug.Log("New guest Login response: UserID: " + userInfo.user_id + "GuestSecret: " + userInfo.guest_secret + "Token: " + userInfo.auth_token);
        this.logOutput.text += "Login response: \nUserID: " + userInfo.user_id + " \nGuestSecret: " + userInfo.guest_secret + " \n";
        
        // Now let's test linking Google Play ID to the guest user
        // NOTE: You're expected to input a valid Google Play single use token here, see the documentation for Unity Google Play integration here: https://docs.unity.com/authentication/en-us/manual/platform-signin-google-play-games
        //       Server will generate an auth ticket with the authorization code received with RequestServerSideAccess
        //       The code will look something like "4/0AbCD..."
        AWSGameSDKClient.Instance.LinkGooglePlayIdToCurrentUser("YourTokenHere", this.OnLinkGooglePlayIdResponse);

        // You would use AWSGameSDKClient.Instance.LoginWithGooglePlayToken to login with an existing Google Play linked identity or to create a new one
    }

    void OnLinkGooglePlayIdResponse(LoginRequestData userInfo)
    {
        Debug.Log("Google Play ID linking response: UserID: " + userInfo.user_id + "GooglePlayID: " + userInfo.google_play_id + "Token: " + userInfo.auth_token);
        this.logOutput.text += "Google Play ID response: \nUserID: " + userInfo.user_id + "\nGooglePlayID: " + userInfo.google_play_id + " \n";
    }
}
