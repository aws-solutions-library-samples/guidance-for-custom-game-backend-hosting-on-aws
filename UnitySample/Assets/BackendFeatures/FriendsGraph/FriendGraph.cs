// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.Networking;

public class FriendsGraph : MonoBehaviour
{
    public string loginEndpointUrl;
    public string friendsGraphIntegrationEndpointUrl;
    public Text logOutput;

    // UI Fields and buttons
    public InputField friendInput;
    public Button AddFriendButton;
    public Button RemoveFriendButton;
    public Button ListFriendsButton;
    public Button ListWhoAddedYouButton;
    public Button ListFriendSuggestionsButton;

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

        // Set the callbacks for the UI buttons
        this.AddFriendButton.onClick.AddListener(this.AddFriend);
        this.RemoveFriendButton.onClick.AddListener(this.RemoveFriend);
        this.ListFriendsButton.onClick.AddListener(this.ListFriends);
        this.ListWhoAddedYouButton.onClick.AddListener(this.ListWhoAddedYou);
        this.ListFriendSuggestionsButton.onClick.AddListener(this.ListFriendSuggestions);
    }

    void AddFriend()
    {
        // BackendGetRequest to set-friend
        AWSGameSDKClient.Instance.BackendGetRequest(this.friendsGraphIntegrationEndpointUrl, "set-friend?friend_id=" + this.friendInput.text, this.OnAddFriendResponse);
    }

    void RemoveFriend()
    {
        AWSGameSDKClient.Instance.BackendGetRequest(this.friendsGraphIntegrationEndpointUrl, "delete-friend?friend_id=" + this.friendInput.text, this.OnRemoveFriendResponse);
    }

    void ListFriends()
    {
        AWSGameSDKClient.Instance.BackendGetRequest(this.friendsGraphIntegrationEndpointUrl, "get-friends?dir=out", this.OnListFriendsResponse);
    }

    void ListWhoAddedYou()
    {
        AWSGameSDKClient.Instance.BackendGetRequest(this.friendsGraphIntegrationEndpointUrl, "get-friends?dir=in", this.OnListWhoAddedYouResponse);
    }

    void ListFriendSuggestions()
    {
        AWSGameSDKClient.Instance.BackendGetRequest(this.friendsGraphIntegrationEndpointUrl, "get-friends?dir=new", this.OnListFriendSuggestionsResponse);
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

        // Remove the first line from the log output if it's longer than 20 lines
        if (this.logOutput.text.Split('\n').Length > 20)
        {
            this.logOutput.text = this.logOutput.text.Substring(this.logOutput.text.IndexOf('\n') + 1);
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

        // Call the set-player API of the frinds graph integration to initialize player in Neptune
        Debug.Log("Calling set-player API");
        AWSGameSDKClient.Instance.BackendGetRequest(this.friendsGraphIntegrationEndpointUrl, "set-player", this.OnSetPlayerResponse);
    }

    void OnSetPlayerResponse(UnityWebRequest response)
    {
        Debug.Log("Set-player response: " + response.downloadHandler.text);
        this.logOutput.text += "Set-player response: " + response.downloadHandler.text + "\n";
    }

    void OnAddFriendResponse(UnityWebRequest response)
    {
        Debug.Log("Add-friend response: " + response.downloadHandler.text);
        this.logOutput.text += "Add-friend response: " + response.downloadHandler.text + "\n";
    }

    void OnRemoveFriendResponse(UnityWebRequest response)
    {
        Debug.Log("Remove-friend response: " + response.downloadHandler.text);
        this.logOutput.text += "Remove-friend response: " + response.downloadHandler.text + "\n";
    }

    void OnListFriendsResponse(UnityWebRequest response)
    {
        Debug.Log("List-friends response: " + response.downloadHandler.text);
        this.logOutput.text += "List-friends response: " + response.downloadHandler.text + "\n";
    }

    void OnListWhoAddedYouResponse(UnityWebRequest response)
    {
        Debug.Log("List-who-added-you response: " + response.downloadHandler.text);
        this.logOutput.text += "List-who-added-you response: " + response.downloadHandler.text + "\n";
    }

    void OnListFriendSuggestionsResponse(UnityWebRequest response)
    {
        Debug.Log("List-friend-suggestions response: " + response.downloadHandler.text);
        this.logOutput.text += "List-friend-suggestions response: " + response.downloadHandler.text + "\n";
    }
}
