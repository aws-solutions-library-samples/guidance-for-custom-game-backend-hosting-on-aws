// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

'use strict';

// AWS-provided JWT verifier
const { JwtRsaVerifier } = require("aws-jwt-verify");

// X-ray for distributed tracing
var AWSXRay = require('aws-xray-sdk');
AWSXRay.config([AWSXRay.plugins.ECSPlugin]);

// AWS SDK with tracing
const AWS = AWSXRay.captureAWS(require('aws-sdk'));

const verifier = JwtRsaVerifier.create({
  issuer: process.env.ISSUER_ENDPOINT, // Get our custom issuer url from environment
  audience: "gamebackend", // set this to the expected "aud" claim to gamebackend
  jwksUri: process.env.ISSUER_ENDPOINT + "/.well-known/jwks.json", // Set the JWKS file path at issuer
  scope: ["guest", "authenticated"], // We accept guest and authenticated scope
});

// Create a Redis client for our ElastiCache Serverless endpoint
const redis = require('redis');
const redisClient = redis.createClient({
  host: process.env.REDIS_ENDPOINT,
  port: 6379,
  socket: {
    host: process.env.REDIS_ENDPOINT,
    port: 6379,
    tls: true
  }
});
redisClient.connect();

redisClient.on('error', (err) => {
  console.error('Redis error:', err);
});

redisClient.on('end', () => {
  console.log('Redis connection closed');
});

redisClient.on('reconnecting', () => {
  console.log('Reconnecting to Redis...');
});


// Map of websockets mapped to userIDs
const websockets = new Map();

// WEBSOCKET SERVER on 80

const WebSocket = require('ws');
const wss = new WebSocket.Server({ port: 80 });
const url = require('url');

wss.on('connection', async (ws, req) => {

  console.log("New websocket connection");

  const params = url.parse(req.url, true);
  // If no token is found, return an error
  if (!params.query.auth_token) {
    // Reject the connection
    ws.send("No authentication token provided");
    ws.close();
  }
  
  try {
    // Note: This is a blocking call and could be non-blocking optimally. With cached keys it's really fast though
    var payload = await verifier.verify(params.query.auth_token);
    console.log("Token is valid");
    // Add the user to the websocket map
    websockets.set(ws, payload.sub);

  } catch (err) {
    console.log(err);
    ws.send("invalid token");
    ws.close();
    return;
  }

  ws.send('Successfully connected!'); // send a message

  // Callback for message handling
  ws.on('message', function message(data) {
    console.log('received: %s', data);
    handleMessage(ws, data);
  });

});

// Message handler function for messages from client through the Websocket
function handleMessage(ws, data) {

    try {
      console.log('received: %s', data);

      // Get the userID from the websocket map
      const userID = websockets.get(ws);

      const dataString = data.toString();

      // Check message type
      if (dataString.startsWith("set-name:")) {
          // Set the user's name in Redis
          const username = dataString.split(":")[1];
          // log the userID and username
          console.log("Setting username for " + userID + " to " + username);
          redisClient.set(userID, username);
      }
      else if (dataString.startsWith("message:")) {
          // NOTE: This needs to be a callback instead
          redisClient.get(userID, (err, username) => {
            if (err) {
              console.error('Redis error:', err);
              ws.send("Error retrieving username");
              return;
            }
            if (!username) {
              ws.send("You must set a username first");
              return;
            }
            // Get the channel we're sending to from data
            const channel = dataString.split(":")[1];
            // Get the message from data
            const message = dataString.split(":")[2];
            // Placeholder: Just send the message and channel and username back
            ws.send("TODO: Message sent to " + channel + " by " + username + ": " + message);
          });
      } 
      else {
          ws.send("Invalid message");
      }
    } catch (err) {
      console.log(err);
      ws.send("Error handling message: " + err);
    }
}

// HEALTH CHECK SERVER on 8080

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
