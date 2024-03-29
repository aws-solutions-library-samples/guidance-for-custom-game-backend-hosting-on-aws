// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;
using System;
using System.Text;
using System.Threading.Tasks;
using System.Net.Http;
using UnityEngine.SceneManagement;

public class MatchmakingRequestData
{
    public string TicketId;
    public string Status;
}

public class MatchmakingStatusData
{
    public string MatchmakingStatus = null;
    public string DnsName;
    public string IpAddress;
    public int Port;
    public string PlayerSessionId;
}

public class AmazonGameLiftIntegration : MonoBehaviour
{
    public string loginEndpointUrl;
    public string gameliftIntegrationBackendEndpointUrl;
    public Text logOutput;


    // Total tries of trying to get match status
    private int totalMatchStatusRequests = 0;

    private string ticketId = null;

    // Latencies
    private Dictionary<string, double> latencies = new Dictionary<string, double>();

    async Task<double> SendHTTPSPingRequest(string requestUrl)
    {
        try {
            //First request to establish the connection
            var request = new HttpRequestMessage
            {
                Method = HttpMethod.Get,
                RequestUri = new Uri(requestUrl),
            };
            // Execute the request
            var client = new HttpClient();
            var resp = await client.SendAsync(request);

            // We measure the average of second and third requests to get the TCP latency without HTTPS handshake
            var startTime = DateTime.Now;
            request = new HttpRequestMessage
            {
                Method = HttpMethod.Get,
                RequestUri = new Uri(requestUrl),
            };
            resp = await client.SendAsync(request);
            request = new HttpRequestMessage
            {
                Method = HttpMethod.Get,
                RequestUri = new Uri(requestUrl),
            };
            resp = await client.SendAsync(request);
            // Total time
            var totalTime = (DateTime.Now - startTime).TotalMilliseconds / 2.0;
            return totalTime;
        }
        catch(Exception e) {
            print("Error reaching the endpoint " + requestUrl + ", setting latency to 1 second");
            return 1000.0;
        }
    }

    // Helper method to measure TCP latencies to the two sample locatiosn we use
    void MeasureLatencies()
    {
        // We'll ping Regions we are using in the sample deployment, you can extend to any amount
        var region1 = "us-east-1";
        var region2 = "us-west-2";
        var region3 = "eu-west-1";

        // Check latencies to Regions by pinging DynamoDB endpoints (they just report health but we use them here for latency)
        var response = Task.Run(() => this.SendHTTPSPingRequest("https://dynamodb."+ region1 + ".amazonaws.com"));
        response.Wait(1000); // We'll expect a response in 1 second
        string latency1 = "Latency " + region1 + ": " + response.Result + " ms";
        this.latencies.Add(region1, response.Result);
        response = Task.Run(() => this.SendHTTPSPingRequest("https://dynamodb." + region2 + ".amazonaws.com"));
        response.Wait(1000); // We'll expect a response in 1 second
        string latency2 = "Latency " + region2 + ": " + response.Result + " ms";
        this.latencies.Add(region2, response.Result);
        response = Task.Run(() => this.SendHTTPSPingRequest("https://dynamodb." + region3 + ".amazonaws.com"));
        response.Wait(1000); // We'll expect a response in 1 second
        string latency3 = "Latency " + region3 + ": " + response.Result + " ms";
        this.latencies.Add(region3, response.Result);

        // Write latencies to logOutput
        this.logOutput.text += latency1 + " ";
        this.logOutput.text += latency2 + " ";
        this.logOutput.text += latency3 + "\n";
    }

    // Start is called before the first frame update
    void Start()
    {
        // Measure the latencies first
        Debug.Log("Measuring latencies");
        this.logOutput.text += "Measuring latencies...\n";
        this.MeasureLatencies();
        
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
        this.logOutput.text += "Login response: \nUserID: " + userInfo.user_id + "\n";
        
        // Store identity to PlayerPrefs
        PlayerPrefs.SetString("user_id", userInfo.user_id);
        PlayerPrefs.SetString("guest_secret", userInfo.guest_secret);
        //PlayerPrefs.SetString("refresh_token", userInfo.refresh_token); // You could use this to login with refresh token instead of logging in a gain
        //PlayerPrefs.SetString("refresh_token_expires_datetime", DateTime.UtcNow.AddSeconds(userInfo.refresh_token_expires_in).ToString()); // You could use this to initialize new full login when running out of time
        PlayerPrefs.Save();

        // Generate a latencyInMs json from the latencies we measured
        string latencyInMs = "{ \"latencyInMs\": { ";
        foreach(var latency in this.latencies)
        {
            latencyInMs += "\"" + latency.Key + "\": " + (int)latency.Value + ", ";
        }
        latencyInMs = latencyInMs.Substring(0, latencyInMs.Length - 2);
        latencyInMs += " } }";
        Debug.Log("LatencyInMs: " + latencyInMs);

        // POST a requestMatchmaking with latency information
        AWSGameSDKClient.Instance.BackendPostRequest(this.gameliftIntegrationBackendEndpointUrl, "request-matchmaking", this.OnRequestMatchmakingResponse, latencyInMs);
    }

    void OnRequestMatchmakingResponse(UnityWebRequest response)
    {
        Debug.Log("Backend request matchmaking response: " + response.downloadHandler.text);
        this.logOutput.text += "Backend request matchmaking response: " + response.downloadHandler.text + "\n";

        // If we got an error code, abort
        if(response.responseCode >= 400)
        {
            Debug.Log("Matchmaking request error, abort");
            return;
        }

        // Deserialize response into MatchmakingRequestData
        MatchmakingRequestData matchmakingRequestData = JsonUtility.FromJson<MatchmakingRequestData>(response.downloadHandler.text);

        // Reset the total tries of getting match status and start trying
        this.totalMatchStatusRequests = 0;

        // Backend Get request to get-match-status with the ticketId extracted from the response
        this.ticketId = matchmakingRequestData.TicketId;
        var queryParameters = new Dictionary<string, string>();
        queryParameters.Add("ticketId", matchmakingRequestData.TicketId);
        AWSGameSDKClient.Instance.BackendGetRequest(this.gameliftIntegrationBackendEndpointUrl, "get-match-status", this.OnGetMatchStatusResponse, queryParameters);
    }

    void OnGetMatchStatusResponse(UnityWebRequest response)
    {
        Debug.Log("Backend get match status response: " + response.downloadHandler.text);
        this.logOutput.text += "Backend get match status response: " + response.downloadHandler.text + "\n";

        MatchmakingStatusData matchmakingStatusData = new MatchmakingStatusData();
        if(response.responseCode >= 400)
        {
            Debug.Log("No match status yet, just set empty data");

        }
        else
        {
            matchmakingStatusData = JsonUtility.FromJson<MatchmakingStatusData>(response.downloadHandler.text);
        }
  
        // If the match is not yet ready or it hasn't failed, timed out or cancelled, keep trying to get the match status
        if(matchmakingStatusData.MatchmakingStatus == null || matchmakingStatusData.MatchmakingStatus == "MatchmakingQueued" || matchmakingStatusData.MatchmakingStatus == "MatchmakingSearching" || matchmakingStatusData.MatchmakingStatus == "PotentialMatchCreated")
        {
            this.totalMatchStatusRequests++;
            if(this.totalMatchStatusRequests < 15)
            {
                var queryParameters = new Dictionary<string, string>();
                queryParameters.Add("ticketId", this.ticketId);

                // Try again with a delay
                StartCoroutine(DelayedCallToGetMatchStatus(this.gameliftIntegrationBackendEndpointUrl, "get-match-status", this.OnGetMatchStatusResponse, queryParameters));
            }
            else
            {
                Debug.Log("Timed out, didn't receive an end state for matchmaking");
            }
        }
        else if(matchmakingStatusData.MatchmakingStatus == "MatchmakingSucceeded")
        {
            Debug.Log("Matchmaking succeeded, connect to game server");
            this.logOutput.text += "Matchmaking succeeded, connect to game server... " + "\n";
            StartCoroutine(ConnectToServer(matchmakingStatusData));
        }
        else
        {  
            Debug.Log("Matchmaking failed");
        }
    }

    IEnumerator DelayedCallToGetMatchStatus(string endpoint, string path, Action<UnityWebRequest> callback, Dictionary<string, string> queryParameters)
    {
        yield return new WaitForSeconds(1.5f);
        AWSGameSDKClient.Instance.BackendGetRequest(this.gameliftIntegrationBackendEndpointUrl, "get-match-status", this.OnGetMatchStatusResponse, queryParameters);
    }

    IEnumerator ConnectToServer(MatchmakingStatusData matchmakingStatusData){
        SimpleServerClient client = new SimpleServerClient(matchmakingStatusData.IpAddress, matchmakingStatusData.Port, this.logOutput);
        
        // Connect to the server and send our player session ID
        client.ConnectToServer();
        this.logOutput.text += "Connected. Sending Player Session ID: " + matchmakingStatusData.PlayerSessionId + "\n";
        client.SendMessage(matchmakingStatusData.PlayerSessionId);

        // Wait for response for up to 10 seconds (should arrive almost immediately)
        for (int i = 0; i < 200; i++)
        {
            string message = client.ReceiveMessage();
            if(message != null)
            {
                Debug.Log("Received message: " + message);
                this.logOutput.text += "Received message from server: " + message + "\n";
                break;
            }
            
            // Wait just 50ms as we'll get the response soon
            yield return new WaitForSeconds(0.05f);
        }
    }
}
