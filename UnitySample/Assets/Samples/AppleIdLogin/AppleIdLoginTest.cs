// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

public class AppleIdLoginTest : MonoBehaviour
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
        
        // Now let's test linking Apple ID
        // NOTE: You're expected to input a valid Apple ID token here, see the documentation for Unity Apple Sign in for details on how to do this: https://docs.unity.com/authentication/en/manual/set-up-apple-signin
        AWSGameSDKClient.Instance.LinkAppleIdToCurrentUser("eyYourTokenHere", this.OnLinkAppleIdResponse);
    }

    void OnLinkAppleIdResponse(LoginRequestData userInfo)
    {
        Debug.Log("Apple ID linking response: UserID: " + userInfo.user_id + "AppleId: " + userInfo.apple_id + "Token: " + userInfo.auth_token);
        this.logOutput.text += "Apple ID linking response: \nUserID: " + userInfo.user_id + "\nAppleId: " + userInfo.apple_id + " \n";

        // Now let's test logging in with this existing Apple ID
        // NOTE: As above, you're expected to have a valid Apple ID token here
        AWSGameSDKClient.Instance.LoginWithAppleIdToken("eyYourTokenHere", this.OnLoginWithAppleIdResponse);
    }

    void OnLoginWithAppleIdResponse(LoginRequestData userInfo)
    {
        Debug.Log("Apple ID login response: UserID: " + userInfo.user_id + "AppleId: " + userInfo.apple_id + "Token: " + userInfo.auth_token);
        this.logOutput.text += "Apple ID login response: \nUserID: " + userInfo.user_id + "\nAppleId: " + userInfo.apple_id + " \nToken: " + userInfo.auth_token + "\n";
    }
}
