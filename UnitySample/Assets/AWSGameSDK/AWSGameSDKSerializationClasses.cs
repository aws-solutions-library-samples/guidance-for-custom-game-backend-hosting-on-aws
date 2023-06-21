// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0


//** SERIALIZATION OBJECTS FOR THE AWS GAME SDK ***

[System.Serializable]
public class LoginRequestData
{
    public string user_id;
    public string guest_secret;
    public string auth_token;
    public string apple_id;
    public string steam_id;
    public string google_play_id;
    public string facebook_id;
    public string refresh_token;
    public int auth_token_expires_in;
    public int refresh_token_expires_in;
}