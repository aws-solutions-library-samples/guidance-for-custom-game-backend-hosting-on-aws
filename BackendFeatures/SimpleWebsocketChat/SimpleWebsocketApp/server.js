// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

'use strict';



// X-ray for distributed tracing
var AWSXRay = require('aws-xray-sdk');
AWSXRay.config([AWSXRay.plugins.ECSPlugin]);

// AWS SDK with tracing
const AWS = AWSXRay.captureAWS(require('aws-sdk'));

// AWS-provided JWT verifier
const { JwtRsaVerifier } = require("aws-jwt-verify");
const verifier = JwtRsaVerifier.create({
  issuer: process.env.ISSUER_ENDPOINT, // Get our custom issuer url from environment
  audience: "gamebackend", // set this to the expected "aud" claim to gamebackend
  jwksUri: process.env.ISSUER_ENDPOINT + "/.well-known/jwks.json", // Set the JWKS file path at issuer
  scope: ["guest", "authenticated"], // We accept guest and authenticated scope
});

// Redis client for our ElastiCache Serverless endpoint
const redis = require('redis');
const redisClient = redis.createClient({
  socket: {
    host: process.env.REDIS_ENDPOINT,
    port: 6379,
    tls: true
  }
});
redisClient.connect();

// Redis client for pubSub
const redisPubSubClient = redis.createClient({
  socket: {
    host: process.env.REDIS_ENDPOINT,
    port: 6379,
    tls: true
  }
});
redisPubSubClient.connect();

redisClient.on('error', (err) => {
  console.error('Redis error:', err);
});

redisClient.on('end', () => {
  console.log('Redis connection closed');
});

redisClient.on('reconnecting', () => {
  console.log('Reconnecting to Redis...');
});

redisPubSubClient.on('error', (err) => {
  console.error('Redis error:', err);
});

redisPubSubClient.on('end', () => {
  console.log('Redis connection closed');
});

redisPubSubClient.on('reconnecting', () => {
  console.log('Reconnecting to Redis...');
});

// Websockets mapped to userIDs
const websockets = new Map();

// Map of maps of websockets mapped to channels
const channelSubscriptions = new Map();

// WEBSOCKET SERVER on 80
const WebSocket = require('ws');
const wss = new WebSocket.Server({ port: 80 });
const url = require('url');

// Callback for pub/sub chat message handling
const listener = (message, channel) => {
  console.log("Received message from pub/sub channel");
  console.log(message, channel);

  // Send the message to all websockets subscribed to this channel
  const channelSubscribers = channelSubscriptions.get(channel);
  if (channelSubscribers) {
    channelSubscribers.forEach((ws) => {
      try {
        ws.send(JSON.stringify({ type: "chat_message_received", payload: { message: message, channel: channel } }));
      } catch (err) {
        console.error("Error sending chat message over websocket: " + err);
      }
    });
  }
};

wss.on('connection', async (ws, req) => {

  console.log("New websocket connection");

  const params = url.parse(req.url, true);
  // If no token is found, return an error
  if (!params.query.auth_token) {
    // Reject the connection
    ws.send(JSON.stringify({ error: "No authentication token provided" }));
    ws.close();
  }
  
  try {
    var payload = await verifier.verify(params.query.auth_token);
    console.log("Token is valid");
    // Add the user to the websocket map
    websockets.set(ws, payload.sub);

  } catch (err) {
    console.log(err);
    ws.send(JSON.stringify({ error: "Invalid token" }));
    ws.close();
    return;
  }

  ws.send(JSON.stringify({ message: 'Successfully connected!' })); // send a message

  // Callback for message handling
  ws.on('message', function message(data) {
    console.log('received: %s', data);
    handleMessage(ws, data);
  });

  // Callback for disconnecting
  ws.on('close', function close() {
    try {
      console.log('User disconnected');
      // Remove user from all subscriptions
      channelSubscriptions.forEach((subscriberMap, key) => {
        subscriberMap.delete(ws);
        // If channel is empty, unsubscribe
        if (subscriberMap.size === 0) {
          // unsubscribe the redis client from the channel
          redisPubSubClient.sUnsubscribe(key, listener);
          console.log("No more people on channel, Unsubscribed server from " + subscriberMap);
        }
      });
      // Remove the user from the websocket map
      websockets.delete(ws);
    } catch (err) {
      console.log("Error disconnecting user: " + err);
    }
  });

});

// Message handler function for messages from client through the Websocket
async function handleMessage(ws, data) {

    try {
      console.log('received: %s', data);

      // Get the userID from the websocket map
      const userID = websockets.get(ws);

      const dataString = data.toString();
      const parsedData = JSON.parse(dataString);

      // Check message type

      // Set new name for the user
      if (parsedData.type === "set-name") {
          // Set the user's name in Redis
          const username = parsedData.payload.username;
          // log the userID and username
          console.log("Setting username for " + userID + " to " + username);
          redisClient.set(userID, username);
          // Send a message back to the client
          ws.send(JSON.stringify({ message: `Username set to ${username}` }));
      }
      
      // Subscribe to a channel
      else if (parsedData.type === "join") {
          // Check if we subscribed already and if not, add to the list
          const channel = parsedData.payload.channel;
          // log the userID and channel
          console.log("Subscribing " + userID + " to " + channel);
          // add the user to the channel's list of subscribers and create one if it doesn't exist
          if (!channelSubscriptions.has(channel)) {
              console.log("Channel subscription not set yet on this server, creating..");
              channelSubscriptions.set(channel, new Set());
              // subscribe the redis client to the channel
              redisPubSubClient.sSubscribe(channel, listener);
              console.log("Done!");
          }
          // add the websocket to the channel's list of subscribers if it's not already there
          if (!channelSubscriptions.get(channel).has(ws)) {
            channelSubscriptions.get(channel).add(ws);
            ws.send(JSON.stringify({ message: `You have joined ${channel}` }));
          }
          else {
            ws.send(JSON.stringify({ message: `You have already joined ${channel}` }));
          }
      }

      // Unsubscribe from a channel
      else if (parsedData.type === "leave") {
          // Get the channel we're unsubscribing from from data
          const channel = parsedData.payload.channel;
          // log the userID and channel
          console.log("Unsubscribing " + userID + " from " + channel);
          // remove the user from the channel's list of subscribers
          channelSubscriptions.get(channel).delete(ws);
          // if there are no more subscribers, unsubscribe from the channel
          if (channelSubscriptions.get(channel).size === 0) {
              console.log("No more subscribers, unsubscribing..");
              // unsubscribe the redis client from the channel
              redisPubSubClient.sUnsubscribe(channel, listener);
              console.log("Done!");
              // remove the channel from the map
              channelSubscriptions.delete(channel);
          }
          ws.send(JSON.stringify({ message: `You have left ${channel}` }));
      }

      // Receive message to a channel
      else if (parsedData.type === "message") {
          const username = await redisClient.get(userID);
          if (!username) {
              ws.send(JSON.stringify({ error: "You must set a username first" }));
              return;
          }
          // log the datastring and ws
          console.log("Message received: " + dataString + " from " + ws);
          // Get the channel we're sending to from data
          const channel = parsedData.payload.channel;
          // Get the message from data
          const message = parsedData.payload.message;
          // Publish to channel
          redisClient.publish(channel, username + ": " + message);
          // Send response to client
          ws.send(JSON.stringify({ message: `Message sent to ${channel}: ${message}` }));
      }
      
      // Any other messages
      else {
          ws.send(JSON.stringify({ error: "Invalid message" }));
      }
    } catch (err) {
      console.log(err);
      ws.send(JSON.stringify({ error: `Error handling message: ${err}` }));
    }
}

// HEALTH CHECK SERVER for load balancer on 8080

const express = require('express');

// Server constants
const PORT = 8080;
const HOST = '0.0.0.0';

// Server app
const app = express();

app.use(AWSXRay.express.openSegment('SimpleWebsocketChat-HealthCheck'));
// health check for root get
app.get('/', (req, res) => {
  res.status(200).json({ statusCode: 200, message: "OK" });
} );
app.use(AWSXRay.express.closeSegment());

// Setup app
app.listen(PORT, HOST, () => {
  console.log(`Running server on http://${HOST}:${PORT}`);
});