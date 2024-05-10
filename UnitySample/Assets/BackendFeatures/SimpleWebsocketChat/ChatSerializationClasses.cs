
using UnityEngine;
using System;

[Serializable]
public class UserNameData {
    public string username;
}

[Serializable]
public class SetUserNameRequest
{
    public string type;
    // Define the payload as a
    public UserNameData payload;
}

[Serializable]
public class ChannelData
{
    public string channel;
}

[Serializable]
public class ChannelRequest
{
    public string type;
    // Define the payload as a
    public ChannelData payload;
}

[Serializable]
public class MessageData
{
    public string message;
    public string channel;
}

[Serializable]
public class SendMessageRequest
{
    public string type;
    // Define the payload as a
    public MessageData payload;
}

