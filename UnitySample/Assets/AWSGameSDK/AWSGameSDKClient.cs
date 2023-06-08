// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using System;

// Define a Unity singleton for the backend integrations
public class AWSGameSDKClient : MonoBehaviour
{
    public static AWSGameSDKClient Instance { get; private set; }

    // Endpoint for the identity and authentication solution
    private string loginEndpoint = null;

    // UserInfo for backend requests
    private LoginRequestData userInfo = null;

    // Callback for client requested login (temporarily set when logging in to send info back to requester)
    private Action<LoginRequestData> LoginCallback = null;

    // Callback for login or token refresh errors, set when initializing the SDK
    private Action<string> LoginErrorCallback = null;

    // Latest expiration info
    private DateTime authTokenExpirationUTC = DateTime.MinValue;
    private DateTime refreshTokenExpirationUTC = DateTime.MinValue;

    /// SINGLETON INITIALIZATION ///

    private void Awake() 
    { 
        // If there is an instance, and it's not me, delete myself.
        if (Instance != null && Instance != this) 
        { 
            Destroy(this); 
        }
        else
        {
            Instance = this;
            DontDestroyOnLoad(this.gameObject);
        }
    }

    public void Init(string loginEndpoint,  Action<string> loginErrorCallback)
    {
        // Add slash to loginEndpoint if it doesn't exist
        if(!loginEndpoint.EndsWith("/"))
        {
            loginEndpoint += "/";
        }

        this.loginEndpoint = loginEndpoint;
        this.LoginErrorCallback = loginErrorCallback;
    }

    public void Update()
    {
        // Check for auth token expiration (we renew 5 seconds before)
        if(this.authTokenExpirationUTC != DateTime.MinValue && DateTime.UtcNow > this.authTokenExpirationUTC.AddSeconds(-5))
        {
            Debug.Log("Auth token expiring, refresh");
            if(this.userInfo != null && this.userInfo.refresh_token != null)
            {
                this.authTokenExpirationUTC = DateTime.MinValue; // Reset and wait for updated value
                this.RefreshAccessToken(null); // We don't have an outside callback so passing null
            }
        }
    }

    // Callback for login, that will set the userInfo for backend requests. This is triggered by both guest and authenticated requests
    private void SdkLoginCallback(UnityWebRequest request, bool isRefresh=false)
    {
        Debug.Log("Received login response: " + request.downloadHandler.text);
        try {
            var loginResponse = JsonUtility.FromJson<LoginRequestData>(request.downloadHandler.text);

            // If this a refresh, we'll only set the access token and refresh token
            if(isRefresh)
            {
                Debug.Log("Refresh only, set auth and refresh tokens");
                this.userInfo.auth_token = loginResponse.auth_token;
                this.userInfo.refresh_token = loginResponse.refresh_token;
                this.userInfo.auth_token_expires_in = loginResponse.auth_token_expires_in;
                this.userInfo.refresh_token_expires_in = loginResponse.refresh_token_expires_in;
            }
            // Else we'll set all data
            else
            {
                // NOTE: guest_secret is only returned for a guest login, it might be empty
                this.userInfo = loginResponse;
            }

            // Set the expiration UTC times
            this.authTokenExpirationUTC = DateTime.UtcNow.AddSeconds(this.userInfo.auth_token_expires_in);
            this.refreshTokenExpirationUTC = DateTime.UtcNow.AddSeconds(this.userInfo.refresh_token_expires_in);;

            Debug.Log("Auth token expires: " + this.authTokenExpirationUTC.ToString() + ", refresh token expires in: " + this.refreshTokenExpirationUTC.ToString());

            if(this.LoginCallback != null)
            {
                // Reset the login callback member before triggering callback (as the receiver might set it again)
                var callbackTemp = this.LoginCallback;
                this.LoginCallback = null;
                // Set login info for backend calls back to the game
                callbackTemp(this.userInfo);
            }
        } catch(Exception e) {
            Debug.Log("Error parsing login response, user credentials not set: " + e.Message);
            this.LoginErrorCallback(e.Message);
        }
    }

    /// IDENTITY AND AUTHENTICATION ///

    public void LoginWithRefreshToken(string refreshToken, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with and existing refresh token");

        if(this.loginEndpoint == null)
        {
            Debug.LogError("Login endpoint not defined");
            this.LoginErrorCallback("Login endpoint not defined");
            return;
        }
        // Set the login callback so that we can return the info back to the game code
        this.LoginCallback = callback;

        // Call the refresh-access-token endpoint with a callback method
        StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "refresh-access-token", this.SdkLoginCallback,
                            new Dictionary<string, string>() { { "refresh_token", refreshToken } }, true));
    }

    // Refresh access token
    public void RefreshAccessToken(Action<LoginRequestData> callback)
    {
        Debug.Log("Refreshing access token");

        if(this.loginEndpoint == null){
            Debug.LogError("Login endpoint not defined");
            return;
        }

        if(this.userInfo == null){
            Debug.LogError("UserInfo not defined");
            return;
        }

        if(this.userInfo.refresh_token == null){
            Debug.LogError("Refresh token not defined");
            return;
        }
    
        // Call the refresh-access-token endpoint with a callback method
        StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "refresh-access-token", this.SdkLoginCallback,
                            new Dictionary<string, string>() { { "refresh_token", this.userInfo.refresh_token } }, true));
    }
    
    // Login as a guest
    public void LoginAsNewGuestUser(Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in as a new guest");
        this.LoginAsGuest(null,null, callback);
    }
    public void LoginAsGuest(string user_id, string guest_secret, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in as a guest");

        if(this.loginEndpoint == null)
        {
            Debug.LogError("Login endpoint not defined");
            this.LoginErrorCallback("Login endpoint not defined");
            return;
        }

        // Set the login callback so that we can return the info back to the game code
        this.LoginCallback = callback;

        // If we don't have a user ID and guest secret defined, we'll create a new user
        if(user_id == null || guest_secret == null)
        {
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-as-guest", this.SdkLoginCallback, null));
        }
        else if(user_id != null && guest_secret != null)
        {
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-as-guest", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "user_id", user_id }, { "guest_secret", guest_secret } }));
        }
        else
        {
            Debug.LogError("Missing user ID or guest secret");
            this.LoginErrorCallback("Missing user ID or guest secret");
        }
    }

    // Logs in with an Apple Id Token and return user info for an existing account or a new account created for this Apple ID
    public void LoginWithAppleIdToken(string appleAuthToken, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with Apple ID auth token");
        this.LoginWithAppleId(appleAuthToken, null, false, callback);
    }

    // Logs in with an Apple Id Token and return user info for an existing account or a new account created for this Apple ID
    public void LinkAppleIdToCurrentUser(string appleAuthToken, Action<LoginRequestData> callback)
    {
        if(this.userInfo == null)
        {
            Debug.LogError("UserInfo not defined, cannot link Apple ID without existing user");
            return;
        }
        Debug.Log("Logging in with Apple ID auth token");
        this.LoginWithAppleId(appleAuthToken, this.userInfo.auth_token, true, callback);
    }

    // Logs in with an Steam Token and return user info for an existing account or a new account created for this Steam ID
    public void LoginWithSteamToken(string steamAuthToken, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with Steam auth token");
        this.LoginWithSteam(steamAuthToken, null, false, callback);
    }

    // Logs in with an Steam Token and return user info for an existing account or a new account created for this Steam ID
    public void LinkSteamIdToCurrentUser(string steamAuthToken, Action<LoginRequestData> callback)
    {
        if(this.userInfo == null)
        {
            Debug.LogError("UserInfo not defined, cannot link Steam ID without existing user");
            return;
        }
        Debug.Log("Logging in with Steam auth token");
        this.LoginWithSteam(steamAuthToken, this.userInfo.auth_token, true, callback);
    }

    // Logs in with a Google Play token and return user info for an existing account or a new account created for this Google Play ID
    public void LoginWithGooglePlayToken(string googlePlayAuthToken, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with GooglePlay auth token");
        this.LoginWithGooglePlay(googlePlayAuthToken, null, false, callback);
    }

    // Logs in with an Google Play Token and return user info for an existing account or a new account created for this Google Play ID
    public void LinkGooglePlayIdToCurrentUser(string googlePlayAuthToken, Action<LoginRequestData> callback)
    {
        if(this.userInfo == null)
        {
            Debug.LogError("UserInfo not defined, cannot link Google Play ID without existing user");
            return;
        }
        Debug.Log("Logging in with GooglePlay auth token");
        this.LoginWithGooglePlay(googlePlayAuthToken, this.userInfo.auth_token, true, callback);
    }

    // Privately called by the public Apple ID login methods
    private void LoginWithAppleId(string appleAuthToken, string authToken, bool linkToExistingUser, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with Apple ID auth token");

        if(this.loginEndpoint == null)
        {
            Debug.LogError("Login endpoint not defined");
            return;
        }

        // Set the login callback so that we can return the info back to the game code
        this.LoginCallback = callback;

        // If we have an existing user and are requesting linking, send a linking request
        if(appleAuthToken != null && authToken != null && linkToExistingUser)
        {
            Debug.Log("Linking Apple ID to existing user");
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-with-apple-id", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "auth_token", authToken }, { "apple_auth_token", appleAuthToken }, { "link_to_existing_user", "Yes"} }));
        }
        // Else we're logging in as a new user or getting our existing Apple ID linked user
        else if(appleAuthToken != null)
        {
            Debug.Log("Logging in with Apple ID auth token");
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-with-apple-id", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "apple_auth_token", appleAuthToken } }));
        }
        else
        {
            Debug.LogError("Missing tokens for requesting appleID");
        }
    }

    // Privately called by the public Steam login methods
    void LoginWithSteam(string steamAuthToken, string authToken, bool linkToExistingUser, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with Steam auth token");

        if(this.loginEndpoint == null)
        {
            Debug.LogError("Login endpoint not defined");
            return;
        }

        // Set the login callback so that we can return the info back to the game code
        this.LoginCallback = callback;

        // If we have an existing user and are requesting linking, send a linking request
        if(steamAuthToken != null && authToken != null && linkToExistingUser)
        {
            Debug.Log("Linking Steam ID to existing user");
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-with-steam", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "auth_token", authToken }, { "steam_auth_token", steamAuthToken }, { "link_to_existing_user", "Yes"} }));
        }
        // Else we're logging in as a new user or getting our existing Apple ID linked user
        else if(steamAuthToken != null)
        {
            Debug.Log("Logging in with Steam auth token");
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-with-steam", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "steam_auth_token", steamAuthToken } }));
        }
        else
        {
            Debug.LogError("Missing tokens for requesting user with Steam token");
        }
    }

    // Privately called by the public Google Play login methods
    void LoginWithGooglePlay(string googlePlayAuthToken, string authToken, bool linkToExistingUser, Action<LoginRequestData> callback)
    {
        Debug.Log("Logging in with GooglePlay auth token");

        if(this.loginEndpoint == null)
        {
            Debug.LogError("Login endpoint not defined");
            return;
        }

        // Set the login callback so that we can return the info back to the game code
        this.LoginCallback = callback;

        // If we have an existing user and are requesting linking, send a linking request
        if(googlePlayAuthToken != null && authToken != null && linkToExistingUser)
        {
            Debug.Log("Linking Google Play to existing user");
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-with-google-play", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "auth_token", authToken }, { "google_play_auth_token", googlePlayAuthToken }, { "link_to_existing_user", "Yes"} }));
        }
        // Else we're logging in as a new user or getting our existing Apple ID linked user
        else if(googlePlayAuthToken != null)
        {
            Debug.Log("Logging in with Google Play auth token");
            // Call CreateNewGuestUser with a callback method
            StartCoroutine(this.CallRestApiGetForLogin(this.loginEndpoint, "login-with-google-play", this.SdkLoginCallback, 
                            new Dictionary<string, string>() { { "google_play_auth_token", googlePlayAuthToken } }));
        }
        else
        {
            Debug.LogError("Missing tokens for requesting user with Google Play token");
        }
    }

    /// BACKEND API REQUEST HELPER METHODS TO CALL CUSTOM BACKEND FUNCTIONALITY ///

    // Authenticated client Get request to backend system
    public void BackendGetRequest(string url, string resource, Action<UnityWebRequest> callback, Dictionary<string, string> getParameters = null)
    {
        if(this.userInfo == null)
        {
            Debug.LogError("UserInfo not defined");
            return;
        }
        // Add slash to url if it doesn't exist
        if(!url.EndsWith("/"))
        {
            url += "/";
        }
        StartCoroutine(this.CallRestApiGetWithAuth(url, resource, this.userInfo.auth_token, callback, getParameters));
    }

    /// GENERAL API REQUEST HELPER METHODS ///

    IEnumerator CallRestApiGetForLogin(string url, string resource, Action<UnityWebRequest, bool> callback, Dictionary<string, string> getParameters = null, bool isRefresh = false)
    {
        // Add the parameters to the URL
        if(getParameters != null)
        {
            resource += "?";
            foreach(KeyValuePair<string, string> parameter in getParameters)
            {
                resource += $"&{parameter.Key}={parameter.Value}";
            }
        }

        using (UnityWebRequest request = UnityWebRequest.Get(url + resource))
        {
            Debug.Log("Sending request: " + url + resource);
            // Send the request and call the callback when we get a response
            yield return request.SendWebRequest();
            callback(request, isRefresh);
        }
    }

    IEnumerator CallRestApiGetWithAuth(string url, string resource, string authToken, Action<UnityWebRequest> callback, Dictionary<string, string> getParameters = null)
    {
        // Add the parameters to the URL
        if(getParameters != null)
        {
            resource += "?";
            foreach(KeyValuePair<string, string> parameter in getParameters)
            {
                resource += $"&{parameter.Key}={parameter.Value}";
            }
        }

        using (UnityWebRequest request = UnityWebRequest.Get(url + resource))
        {
            // Set the auth token
            request.SetRequestHeader("Authorization", authToken);

            Debug.Log("Sending request: " + url + resource);
            // Send the request and call the callback when we get a response
            yield return request.SendWebRequest();
            callback(request);
        }
    }
}

